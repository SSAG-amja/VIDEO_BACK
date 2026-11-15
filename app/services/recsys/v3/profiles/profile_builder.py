from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.services.recsys.v3.domain.behavior import (
    SnapshotAction,
    SnapshotSignal,
    append_current_signal,
    normalize_datetime,
    recency_multiplier,
    signal_sort_key,
)
from app.models.mapping import (
    PlaylistMovie,
    UserInteraction,
    user_favorite_movies,
    user_genres,
    user_otts,
)
from app.models.movie import Movie
from app.models.ontology import OntologyBuild
from app.models.playlist import Playlist
from app.models.user import User
from app.services.recsys.v3.config import (
    PREFERENCE_SHIFT_DISTANCE_NOISE_FLOOR,
    PREFERENCE_SHIFT_DRIFT_DISTANCE_THRESHOLD,
    PREFERENCE_SHIFT_MIN_HISTORICAL_MOVIES,
    PREFERENCE_SHIFT_PRIMARY_FAMILY_WEIGHTS,
    PREFERENCE_SHIFT_PRIMARY_GROUP_WEIGHT,
    PREFERENCE_SHIFT_SECONDARY_FAMILY_WEIGHTS,
    PREFERENCE_SHIFT_SECONDARY_ONLY_CAP,
    PROFILE_ACTION_STRENGTHS,
    PROFILE_EVIDENCE_PER_FEATURE,
    PROFILE_FEATURE_SCORE_CAPS,
    PROFILE_FEATURE_TOP_K,
    SHORT_TERM_FEATURE_TOP_K,
    SHORT_TERM_HALF_LIFE_DAYS,
    SHORT_TERM_MAX_ACTIONS,
    SHORT_TERM_MIN_DISTINCT_POSITIVE_MOVIES,
    SHORT_TERM_STRONG_MIN_DISTINCT_MOVIES,
    SHORT_TERM_STRONG_MIN_WEIGHT,
    SHORT_TERM_WINDOW_DAYS,
)
from app.services.recsys.v3.domain.catalog import eligible_catalog_movie_clause
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.domain.ontology_registry import (
    ONTOLOGY_ENGINE_NAME,
    ONTOLOGY_SCHEMA_VERSION,
)
from app.services.recsys.v3.domain.schemas import (
    FeatureDirection,
    LongTermProfile,
    OnboardingProfile,
    OttFilterMode,
    ProfileFamilyDiagnostics,
    ProfileFeatureEvidence,
    ProfileFeatureSignal,
    ProfileMaturity,
    RuntimeProfileBuildResult,
    RuntimeProfileDiagnostics,
    ServingContext,
    ShortTermProfile,
    ShortTermPreferenceState,
    UserProfileBundle,
)


PROFILE_FEATURE_ORDER = (
    FeatureName.GENRE,
    FeatureName.KEYWORD,
    FeatureName.ACTOR,
    FeatureName.DIRECTOR,
    FeatureName.THEME,
    FeatureName.MOOD,
)
RELATION_FEATURES = {
    "has_genre": FeatureName.GENRE,
    "has_keyword": FeatureName.KEYWORD,
    "has_actor": FeatureName.ACTOR,
    "has_director": FeatureName.DIRECTOR,
    "has_theme": FeatureName.THEME,
    "has_mood": FeatureName.MOOD,
}
POSITIVE_PROFILE_ACTIONS = frozenset(
    {
        SnapshotAction.FAVORITE,
        SnapshotAction.WATCHED,
        SnapshotAction.SAVED,
        SnapshotAction.PINNED,
    }
)
SHORT_TERM_ACTIONS = frozenset(
    {
        SnapshotAction.WATCHED,
        SnapshotAction.SAVED,
        SnapshotAction.PINNED,
        SnapshotAction.PASSED,
    }
)


@dataclass(frozen=True, slots=True)
class GraphProfileEdge:
    ontology_build_id: int
    edge_id: int
    movie_id: int
    relation_type: str
    feature: FeatureName
    ref_id: str
    edge_strength: float
    family_size: int

    def __post_init__(self) -> None:
        if self.ontology_build_id <= 0 or self.edge_id <= 0 or self.movie_id <= 0:
            raise ValueError("graph profile edge IDs must be positive")
        if self.relation_type not in RELATION_FEATURES:
            raise ValueError("graph profile edge relation is not profile-enabled")
        if RELATION_FEATURES[self.relation_type] != self.feature:
            raise ValueError("graph profile edge relation/feature mismatch")
        if not self.ref_id.strip():
            raise ValueError("graph profile edge ref_id cannot be empty")
        if not math.isfinite(self.edge_strength) or not 0.0 <= self.edge_strength <= 1.0:
            raise ValueError("graph profile edge strength must be between 0 and 1")
        if self.family_size <= 0:
            raise ValueError("graph profile edge family size must be positive")


@dataclass(frozen=True, slots=True)
class PreferenceShiftAssessment:
    state: ShortTermPreferenceState
    recent_evidence_confidence: float
    semantic_distance: float
    drift_confidence: float
    components: dict[str, float]


@dataclass(slots=True)
class _FeatureAccumulator:
    raw_score: float = 0.0
    contribution_count: int = 0
    latest_at: datetime | None = None
    actions: set[str] = field(default_factory=set)
    evidence: list[ProfileFeatureEvidence] = field(default_factory=list)


def build_user_runtime_profile(
    db: Session,
    *,
    user_id: int,
    ontology_build_id: int,
    as_of: datetime | None = None,
    model_user_known: bool = False,
    ott_mode: OttFilterMode = OttFilterMode.ALL,
) -> RuntimeProfileBuildResult:
    started = time.monotonic()
    cutoff_at = normalize_datetime(as_of or datetime.now())
    validate_profile_build(db, ontology_build_id)
    user_exists = db.scalar(
        select(User.id).where(User.id == user_id, User.deleted_at.is_(None))
    )
    if user_exists is None:
        raise ValueError(f"runtime profile user does not exist user_id={user_id}")

    signals = load_user_profile_signals(db, user_id=user_id, data_cutoff_at=cutoff_at)
    genre_ids = frozenset(
        int(value)
        for value in db.scalars(
            select(user_genres.c.genre_id).where(user_genres.c.user_id == user_id)
        )
    )
    ott_ids = frozenset(
        int(value)
        for value in db.scalars(
            select(user_otts.c.ott_id).where(user_otts.c.user_id == user_id)
        )
    )
    source_movie_ids = frozenset(signal.movie_id for signal in signals)
    edges_by_movie = load_graph_profile_edges(
        db,
        ontology_build_id=ontology_build_id,
        movie_ids=source_movie_ids,
    )
    result = assemble_user_runtime_profile(
        user_id=user_id,
        ontology_build_id=ontology_build_id,
        as_of=cutoff_at,
        signals=signals,
        onboarding_genre_ids=genre_ids,
        subscribed_ott_ids=ott_ids,
        edges_by_movie=edges_by_movie,
        model_user_known=model_user_known,
        ott_mode=ott_mode,
    )
    return RuntimeProfileBuildResult(
        bundle=result.bundle,
        diagnostics=replace(
            result.diagnostics,
            elapsed_seconds=round(time.monotonic() - started, 6),
        ),
    )


def assemble_user_runtime_profile(
    *,
    user_id: int,
    ontology_build_id: int,
    as_of: datetime,
    signals: Iterable[SnapshotSignal],
    onboarding_genre_ids: frozenset[int],
    subscribed_ott_ids: frozenset[int],
    edges_by_movie: dict[int, tuple[GraphProfileEdge, ...]],
    model_user_known: bool,
    ott_mode: OttFilterMode,
) -> RuntimeProfileBuildResult:
    cutoff_at = normalize_datetime(as_of)
    sorted_signals = tuple(sorted(signals, key=signal_sort_key))
    if any(signal.user_id != user_id for signal in sorted_signals):
        raise ValueError("runtime profile signals contain a mismatched user ID")
    positive_signals, negative_signals = resolve_directional_signals(sorted_signals)
    watched_movie_ids = frozenset(
        signal.movie_id
        for signal in sorted_signals
        if signal.action == SnapshotAction.WATCHED
    )
    positive_movie_ids = frozenset(signal.movie_id for signal in positive_signals)
    negative_movie_ids = frozenset(signal.movie_id for signal in negative_signals)
    excluded_movie_ids = watched_movie_ids | negative_movie_ids

    long_positive, long_positive_diagnostics = aggregate_profile_features(
        positive_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.POSITIVE,
        as_of=cutoff_at,
        decay=long_term_decay,
        top_k=PROFILE_FEATURE_TOP_K,
    )
    long_negative, long_negative_diagnostics = aggregate_profile_features(
        negative_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.NEGATIVE,
        as_of=cutoff_at,
        decay=long_term_decay,
        top_k=PROFILE_FEATURE_TOP_K,
    )

    short_signals = select_short_term_signals(sorted_signals, as_of=cutoff_at)
    short_positive_signals, short_negative_signals = resolve_directional_signals(short_signals)
    short_positive, short_positive_diagnostics = aggregate_profile_features(
        short_positive_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.POSITIVE,
        as_of=cutoff_at,
        decay=short_term_decay,
        top_k=SHORT_TERM_FEATURE_TOP_K,
    )
    short_negative, short_negative_diagnostics = aggregate_profile_features(
        short_negative_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.NEGATIVE,
        as_of=cutoff_at,
        decay=short_term_decay,
        top_k=SHORT_TERM_FEATURE_TOP_K,
    )
    historical_positive_signals = tuple(
        signal
        for signal in positive_signals
        if signal not in short_positive_signals
    )
    historical_positive, _ = aggregate_profile_features(
        historical_positive_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.POSITIVE,
        as_of=cutoff_at,
        decay=long_term_decay,
        top_k=PROFILE_FEATURE_TOP_K,
    )
    recent_positive_movie_weights = positive_movie_weights(short_positive_signals)
    shift_assessment = assess_preference_shift(
        recent_positive=short_positive,
        historical_positive=historical_positive,
        recent_positive_movie_count=len(recent_positive_movie_weights),
        recent_positive_weight_sum=sum(recent_positive_movie_weights.values()),
        historical_positive_movie_count=len(
            {signal.movie_id for signal in historical_positive_signals}
        ),
        recent_negative_action_count=len(short_negative_signals),
    )

    favorite_movie_ids = frozenset(
        signal.movie_id
        for signal in sorted_signals
        if signal.action == SnapshotAction.FAVORITE
        and signal.movie_id not in negative_movie_ids
    )
    favorite_signals = tuple(
        signal
        for signal in positive_signals
        if signal.action == SnapshotAction.FAVORITE
    )
    onboarding_priors, _ = aggregate_profile_features(
        favorite_signals,
        edges_by_movie=edges_by_movie,
        ontology_build_id=ontology_build_id,
        direction=FeatureDirection.POSITIVE,
        as_of=cutoff_at,
        decay=long_term_decay,
        top_k=PROFILE_FEATURE_TOP_K,
    )

    onboarding = OnboardingProfile(
        user_id=user_id,
        favorite_movie_ids=favorite_movie_ids,
        genre_ids=onboarding_genre_ids,
        derived_feature_priors=onboarding_priors,
    )
    long_term = LongTermProfile(
        user_id=user_id,
        as_of=cutoff_at,
        maturity=profile_maturity(
            positive_pair_count=len(positive_movie_ids),
            has_onboarding=bool(favorite_movie_ids or onboarding_genre_ids),
        ),
        model_user_known=model_user_known,
        positive_movie_ids=positive_movie_ids,
        negative_movie_ids=negative_movie_ids,
        excluded_movie_ids=excluded_movie_ids,
        positive_features=long_positive,
        negative_features=long_negative,
        positive_pair_count=len(positive_movie_ids),
        passed_pair_count=len(negative_movie_ids),
        watched_pair_count=len(watched_movie_ids),
    )
    short_term = ShortTermProfile(
        user_id=user_id,
        as_of=cutoff_at,
        window_action_count=len(short_positive_signals) + len(short_negative_signals),
        drift_confidence=shift_assessment.drift_confidence,
        preference_state=shift_assessment.state,
        recent_evidence_confidence=shift_assessment.recent_evidence_confidence,
        semantic_distance=shift_assessment.semantic_distance,
        recent_positive_movie_ids=frozenset(
            signal.movie_id for signal in short_positive_signals
        ),
        recent_negative_movie_ids=frozenset(
            signal.movie_id for signal in short_negative_signals
        ),
        positive_features=short_positive,
        negative_features=short_negative,
    )
    serving_context = ServingContext(
        user_id=user_id,
        ott_mode=ott_mode,
        availability_as_of=cutoff_at,
        subscribed_ott_ids=subscribed_ott_ids,
    )
    graph_movie_ids = frozenset(edges_by_movie)
    feature_uncovered_movie_ids = tuple(
        sorted((positive_movie_ids | negative_movie_ids) - graph_movie_ids)
    )
    bundle = UserProfileBundle(
        user_id=user_id,
        onboarding=onboarding,
        long_term=long_term,
        short_term=short_term,
        serving_context=serving_context,
    )
    diagnostics = RuntimeProfileDiagnostics(
        ontology_build_id=ontology_build_id,
        elapsed_seconds=0.0,
        source_action_count=len(sorted_signals),
        graph_source_movie_count=len(positive_movie_ids | negative_movie_ids),
        graph_covered_movie_count=len((positive_movie_ids | negative_movie_ids) & graph_movie_ids),
        feature_uncovered_movie_ids=feature_uncovered_movie_ids,
        long_term_families=long_positive_diagnostics + long_negative_diagnostics,
        short_term_families=short_positive_diagnostics + short_negative_diagnostics,
        drift_components=shift_assessment.components,
    )
    return RuntimeProfileBuildResult(bundle=bundle, diagnostics=diagnostics)


def build_onboarding_feature_signals(
    onboarding: OnboardingProfile,
) -> tuple[ProfileFeatureSignal, ...]:
    signals = {
        (signal.feature, signal.ref_id): signal
        for signal in onboarding.derived_feature_priors
    }
    for genre_id in onboarding.genre_ids:
        key = (FeatureName.GENRE, str(genre_id))
        existing = signals.get(key)
        if existing is None:
            signals[key] = ProfileFeatureSignal(
                feature=FeatureName.GENRE,
                ref_id=str(genre_id),
                direction=FeatureDirection.POSITIVE,
                score=1.0,
                raw_score=1.0,
            )
            continue
        if existing.score < 1.0:
            signals[key] = replace(
                existing,
                score=1.0,
                raw_score=max(existing.raw_score or existing.score, 1.0),
            )
    return tuple(
        signals[key]
        for key in sorted(signals, key=lambda item: (item[0].value, item[1]))
    )


def aggregate_profile_features(
    signals: Iterable[SnapshotSignal],
    *,
    edges_by_movie: dict[int, tuple[GraphProfileEdge, ...]],
    ontology_build_id: int,
    direction: FeatureDirection,
    as_of: datetime,
    decay: Callable[[SnapshotSignal, datetime], float],
    top_k: dict[str, int],
) -> tuple[tuple[ProfileFeatureSignal, ...], tuple[ProfileFamilyDiagnostics, ...]]:
    accumulators: dict[tuple[FeatureName, str], _FeatureAccumulator] = defaultdict(
        _FeatureAccumulator
    )
    for signal in signals:
        if direction == FeatureDirection.POSITIVE and signal.action not in POSITIVE_PROFILE_ACTIONS:
            raise ValueError("positive profile aggregation received a non-positive action")
        if direction == FeatureDirection.NEGATIVE and signal.action != SnapshotAction.PASSED:
            raise ValueError("negative profile aggregation requires passed actions")
        action_strength = PROFILE_ACTION_STRENGTHS[signal.action.value]
        recency = decay(signal, as_of)
        for edge in edges_by_movie.get(signal.movie_id, ()):
            if edge.ontology_build_id != ontology_build_id:
                raise ValueError("profile edge belongs to a different ontology build")
            if edge.movie_id != signal.movie_id:
                raise ValueError("profile edge is indexed under a different movie")
            normalizer = 1.0 / math.sqrt(edge.family_size)
            contribution = action_strength * recency * edge.edge_strength * normalizer
            if contribution <= 0:
                continue
            evidence = ProfileFeatureEvidence(
                ontology_build_id=ontology_build_id,
                edge_id=edge.edge_id,
                relation_type=edge.relation_type,
                source_movie_id=signal.movie_id,
                action=signal.action.value,
                direction=direction,
                occurred_at=signal.occurred_at,
                action_strength=round(action_strength, 8),
                recency_multiplier=round(recency, 8),
                edge_strength=round(edge.edge_strength, 8),
                family_normalizer=round(normalizer, 8),
                contribution=round(contribution, 8),
            )
            accumulator = accumulators[(edge.feature, edge.ref_id)]
            accumulator.raw_score += contribution
            accumulator.contribution_count += 1
            accumulator.actions.add(signal.action.value)
            if signal.occurred_at is not None and (
                accumulator.latest_at is None or signal.occurred_at > accumulator.latest_at
            ):
                accumulator.latest_at = signal.occurred_at
            accumulator.evidence.append(evidence)
            accumulator.evidence.sort(
                key=lambda item: (-item.contribution, item.source_movie_id, item.edge_id)
            )
            del accumulator.evidence[PROFILE_EVIDENCE_PER_FEATURE:]

    output: list[ProfileFeatureSignal] = []
    diagnostics: list[ProfileFamilyDiagnostics] = []
    for feature in PROFILE_FEATURE_ORDER:
        family_values = [
            (ref_id, accumulator)
            for (item_feature, ref_id), accumulator in accumulators.items()
            if item_feature == feature
        ]
        family_values.sort(
            key=lambda item: (-item[1].raw_score, ref_id_sort_key(item[0]))
        )
        family_top_k = top_k[feature.value]
        retained = family_values[:family_top_k]
        score_cap = PROFILE_FEATURE_SCORE_CAPS[feature.value]
        for ref_id, accumulator in retained:
            evidence = tuple(accumulator.evidence)
            output.append(
                ProfileFeatureSignal(
                    feature=feature,
                    ref_id=ref_id,
                    direction=direction,
                    score=round(min(accumulator.raw_score, score_cap), 8),
                    raw_score=round(accumulator.raw_score, 8),
                    contribution_count=accumulator.contribution_count,
                    source_movie_ids=frozenset(item.source_movie_id for item in evidence),
                    source_actions=tuple(sorted(accumulator.actions)),
                    latest_at=accumulator.latest_at,
                    evidence=evidence,
                )
            )
        diagnostics.append(
            ProfileFamilyDiagnostics(
                feature=feature,
                direction=direction,
                source_edge_count=sum(item.contribution_count for _, item in family_values),
                source_value_count=len(family_values),
                retained_value_count=len(retained),
                dropped_value_count=len(family_values) - len(retained),
                top_k=family_top_k,
                score_cap=score_cap,
            )
        )
    return tuple(output), tuple(diagnostics)


def resolve_directional_signals(
    signals: Iterable[SnapshotSignal],
) -> tuple[tuple[SnapshotSignal, ...], tuple[SnapshotSignal, ...]]:
    by_movie: dict[int, list[SnapshotSignal]] = defaultdict(list)
    for signal in signals:
        by_movie[signal.movie_id].append(signal)
    positive: list[SnapshotSignal] = []
    negative: list[SnapshotSignal] = []
    for movie_id in sorted(by_movie):
        movie_signals = by_movie[movie_id]
        passed = [item for item in movie_signals if item.action == SnapshotAction.PASSED]
        if passed:
            negative.extend(passed)
            continue
        positive.extend(item for item in movie_signals if item.action in POSITIVE_PROFILE_ACTIONS)
    return (
        tuple(sorted(positive, key=signal_sort_key)),
        tuple(sorted(negative, key=signal_sort_key)),
    )


def select_short_term_signals(
    signals: Iterable[SnapshotSignal],
    *,
    as_of: datetime,
) -> tuple[SnapshotSignal, ...]:
    cutoff_at = normalize_datetime(as_of)
    oldest_at = cutoff_at - timedelta(days=SHORT_TERM_WINDOW_DAYS)
    eligible = [
        signal
        for signal in signals
        if signal.action in SHORT_TERM_ACTIONS
        and signal.occurred_at is not None
        and oldest_at <= normalize_datetime(signal.occurred_at) <= cutoff_at
    ]
    eligible.sort(
        key=lambda item: (
            normalize_datetime(item.occurred_at),
            item.movie_id,
            item.action.value,
        ),
        reverse=True,
    )
    return tuple(eligible[:SHORT_TERM_MAX_ACTIONS])


def assess_preference_shift(
    *,
    recent_positive: tuple[ProfileFeatureSignal, ...],
    historical_positive: tuple[ProfileFeatureSignal, ...],
    recent_positive_movie_count: int,
    recent_positive_weight_sum: float,
    historical_positive_movie_count: int,
    recent_negative_action_count: int,
) -> PreferenceShiftAssessment:
    if recent_positive_movie_count < 0 or historical_positive_movie_count < 0:
        raise ValueError("preference-shift movie counts cannot be negative")
    if not math.isfinite(recent_positive_weight_sum) or recent_positive_weight_sum < 0:
        raise ValueError("recent positive weight must be finite and non-negative")
    if recent_negative_action_count < 0:
        raise ValueError("recent negative action count cannot be negative")

    count_support = min(
        1.0,
        recent_positive_movie_count / SHORT_TERM_MIN_DISTINCT_POSITIVE_MOVIES,
    )
    strong_support = (
        min(1.0, recent_positive_weight_sum / SHORT_TERM_STRONG_MIN_WEIGHT)
        if recent_positive_movie_count >= SHORT_TERM_STRONG_MIN_DISTINCT_MOVIES
        else 0.0
    )
    evidence_confidence = max(count_support, strong_support)
    evidence_ready = (
        recent_positive_movie_count >= SHORT_TERM_MIN_DISTINCT_POSITIVE_MOVIES
        or (
            recent_positive_movie_count >= SHORT_TERM_STRONG_MIN_DISTINCT_MOVIES
            and recent_positive_weight_sum >= SHORT_TERM_STRONG_MIN_WEIGHT
        )
    )

    family_distances = {
        feature: feature_distribution_distance(
            recent_positive,
            historical_positive,
            feature=feature,
        )
        for feature in PROFILE_FEATURE_ORDER
    }
    primary_distance, primary_count = weighted_group_distance(
        family_distances,
        PREFERENCE_SHIFT_PRIMARY_FAMILY_WEIGHTS,
    )
    secondary_distance, secondary_count = weighted_group_distance(
        family_distances,
        PREFERENCE_SHIFT_SECONDARY_FAMILY_WEIGHTS,
    )
    if primary_count:
        if secondary_count:
            semantic_distance = (
                PREFERENCE_SHIFT_PRIMARY_GROUP_WEIGHT * primary_distance
                + (1.0 - PREFERENCE_SHIFT_PRIMARY_GROUP_WEIGHT) * secondary_distance
            )
        else:
            semantic_distance = primary_distance
    elif secondary_count:
        semantic_distance = min(
            PREFERENCE_SHIFT_SECONDARY_ONLY_CAP,
            secondary_distance * PREFERENCE_SHIFT_SECONDARY_ONLY_CAP,
        )
    else:
        semantic_distance = 0.0

    history_ready = historical_positive_movie_count >= PREFERENCE_SHIFT_MIN_HISTORICAL_MOVIES
    if not evidence_ready:
        state = ShortTermPreferenceState.INACTIVE
        drift_confidence = 0.0
    elif not history_ready or not primary_count:
        state = ShortTermPreferenceState.RECENT_INTEREST
        drift_confidence = 0.0
    else:
        calibrated_distance = max(
            0.0,
            (semantic_distance - PREFERENCE_SHIFT_DISTANCE_NOISE_FLOOR)
            / (1.0 - PREFERENCE_SHIFT_DISTANCE_NOISE_FLOOR),
        )
        drift_confidence = min(1.0, evidence_confidence * calibrated_distance)
        state = (
            ShortTermPreferenceState.DRIFT
            if semantic_distance >= PREFERENCE_SHIFT_DRIFT_DISTANCE_THRESHOLD
            else ShortTermPreferenceState.STABLE
        )

    components = {
        "recent_evidence_confidence": round(evidence_confidence, 8),
        "recent_positive_movie_count": float(recent_positive_movie_count),
        "recent_positive_weight_sum": round(recent_positive_weight_sum, 8),
        "recent_negative_action_count": float(recent_negative_action_count),
        "historical_positive_movie_count": float(historical_positive_movie_count),
        "primary_semantic_distance": round(primary_distance, 8),
        "secondary_semantic_distance": round(secondary_distance, 8),
        "semantic_distance": round(semantic_distance, 8),
        "distance_noise_floor": PREFERENCE_SHIFT_DISTANCE_NOISE_FLOOR,
        "comparable_primary_family_count": float(primary_count),
        "comparable_secondary_family_count": float(secondary_count),
        "drift_distance_threshold": PREFERENCE_SHIFT_DRIFT_DISTANCE_THRESHOLD,
    }
    for feature, distance in family_distances.items():
        if distance is not None:
            components[f"{feature.value}_distance"] = round(distance, 8)
    return PreferenceShiftAssessment(
        state=state,
        recent_evidence_confidence=round(evidence_confidence, 8),
        semantic_distance=round(semantic_distance, 8),
        drift_confidence=round(drift_confidence, 8),
        components=components,
    )


def calculate_drift_confidence(
    *,
    recent_positive: tuple[ProfileFeatureSignal, ...],
    historical_positive: tuple[ProfileFeatureSignal, ...],
    recent_positive_action_count: int,
    recent_negative_action_count: int,
) -> tuple[float, dict[str, float]]:
    """Backward-compatible wrapper for callers that only have action counts."""
    assessment = assess_preference_shift(
        recent_positive=recent_positive,
        historical_positive=historical_positive,
        recent_positive_movie_count=recent_positive_action_count,
        recent_positive_weight_sum=float(recent_positive_action_count),
        historical_positive_movie_count=PREFERENCE_SHIFT_MIN_HISTORICAL_MOVIES,
        recent_negative_action_count=recent_negative_action_count,
    )
    return assessment.drift_confidence, assessment.components


def positive_movie_weights(signals: Iterable[SnapshotSignal]) -> dict[int, float]:
    weights: dict[int, float] = {}
    for signal in signals:
        if signal.action not in POSITIVE_PROFILE_ACTIONS:
            continue
        weights[signal.movie_id] = max(
            weights.get(signal.movie_id, 0.0),
            PROFILE_ACTION_STRENGTHS[signal.action.value],
        )
    return weights


def feature_distribution_distance(
    recent: tuple[ProfileFeatureSignal, ...],
    historical: tuple[ProfileFeatureSignal, ...],
    *,
    feature: FeatureName,
) -> float | None:
    recent_scores = {
        item.ref_id: item.score for item in recent if item.feature == feature and item.score > 0
    }
    historical_scores = {
        item.ref_id: item.score
        for item in historical
        if item.feature == feature and item.score > 0
    }
    if not recent_scores or not historical_scores:
        return None
    recent_total = sum(recent_scores.values())
    historical_total = sum(historical_scores.values())
    refs = set(recent_scores) | set(historical_scores)
    overlap = sum(
        min(
            recent_scores.get(ref_id, 0.0) / recent_total,
            historical_scores.get(ref_id, 0.0) / historical_total,
        )
        for ref_id in refs
    )
    return max(0.0, min(1.0, 1.0 - overlap))


def weighted_group_distance(
    family_distances: dict[FeatureName, float | None],
    weights: dict[str, float],
) -> tuple[float, int]:
    comparable = [
        (feature, distance)
        for feature, distance in family_distances.items()
        if distance is not None and feature.value in weights
    ]
    if not comparable:
        return 0.0, 0
    total_weight = sum(weights[feature.value] for feature, _distance in comparable)
    distance = sum(
        weights[feature.value] * float(value) for feature, value in comparable
    ) / total_weight
    return distance, len(comparable)


def profile_maturity(*, positive_pair_count: int, has_onboarding: bool) -> ProfileMaturity:
    if positive_pair_count == 0:
        return ProfileMaturity.ONBOARDING_ONLY if has_onboarding else ProfileMaturity.NO_PROFILE
    if positive_pair_count <= 2:
        return ProfileMaturity.SPARSE
    if positive_pair_count <= 9:
        return ProfileMaturity.LIGHT
    return ProfileMaturity.ESTABLISHED


def long_term_decay(signal: SnapshotSignal, as_of: datetime) -> float:
    return recency_multiplier(
        signal.occurred_at,
        data_cutoff_at=as_of,
        action=signal.action,
    )


def short_term_decay(signal: SnapshotSignal, as_of: datetime) -> float:
    if signal.occurred_at is None:
        return 0.0
    age_seconds = max(
        (
            normalize_datetime(as_of)
            - normalize_datetime(signal.occurred_at)
        ).total_seconds(),
        0.0,
    )
    age_days = age_seconds / 86400.0
    return 0.5 ** (age_days / SHORT_TERM_HALF_LIFE_DAYS)


def validate_profile_build(db: Session, ontology_build_id: int) -> OntologyBuild:
    if ontology_build_id <= 0:
        raise ValueError("ontology build ID must be positive")
    build = db.get(OntologyBuild, ontology_build_id)
    if build is None:
        raise ValueError(f"ontology build does not exist build_id={ontology_build_id}")
    if build.engine_name != ONTOLOGY_ENGINE_NAME or build.schema_version != ONTOLOGY_SCHEMA_VERSION:
        raise ValueError("runtime profile requires a V3 ontology build")
    if build.status != "success":
        raise ValueError(
            f"runtime profile requires a successful ontology build build_id={ontology_build_id} "
            f"status={build.status}"
        )
    return build


def load_graph_profile_edges(
    db: Session,
    *,
    ontology_build_id: int,
    movie_ids: frozenset[int],
) -> dict[int, tuple[GraphProfileEdge, ...]]:
    if not movie_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT edge.id,
                   source.ref_id::bigint AS movie_id,
                   edge.relation_type,
                   target.ref_id,
                   COALESCE(edge.effective_strength, edge.weight * edge.confidence) AS edge_strength,
                   count(*) OVER (
                       PARTITION BY source.id, edge.relation_type
                   )::integer AS family_size
            FROM ontology_edges edge
            JOIN ontology_nodes source
              ON source.id = edge.source_node_id
             AND source.build_id = :build_id
             AND source.node_type = 'movie'
            JOIN ontology_nodes target
              ON target.id = edge.target_node_id
             AND target.build_id = :build_id
            WHERE edge.build_id = :build_id
              AND source.ref_id = ANY(:movie_ref_ids)
              AND edge.relation_type = ANY(:relation_types)
            ORDER BY source.ref_id::bigint, edge.relation_type, target.ref_id, edge.id
            """
        ).execution_options(stream_results=True, yield_per=5_000),
        {
            "build_id": ontology_build_id,
            "movie_ref_ids": [str(movie_id) for movie_id in sorted(movie_ids)],
            "relation_types": sorted(RELATION_FEATURES),
        },
    )
    edges: dict[int, list[GraphProfileEdge]] = defaultdict(list)
    for edge_id, movie_id, relation_type, ref_id, edge_strength, family_size in rows:
        strength = float(edge_strength)
        if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
            raise ValueError(f"invalid profile edge strength edge_id={edge_id}")
        edges[int(movie_id)].append(
            GraphProfileEdge(
                ontology_build_id=ontology_build_id,
                edge_id=int(edge_id),
                movie_id=int(movie_id),
                relation_type=relation_type,
                feature=RELATION_FEATURES[relation_type],
                ref_id=str(ref_id),
                edge_strength=strength,
                family_size=int(family_size),
            )
        )
    return {movie_id: tuple(items) for movie_id, items in edges.items()}


def load_user_profile_signals(
    db: Session,
    *,
    user_id: int,
    data_cutoff_at: datetime,
) -> tuple[SnapshotSignal, ...]:
    cutoff_at = normalize_datetime(data_cutoff_at)
    signals: list[SnapshotSignal] = []
    favorite_movie_ids = db.scalars(
        select(user_favorite_movies.c.movie_id)
        .join(Movie, Movie.id == user_favorite_movies.c.movie_id)
        .where(
            user_favorite_movies.c.user_id == user_id,
            *eligible_catalog_movie_clause(),
        )
    )
    signals.extend(
        SnapshotSignal(
            user_id=user_id,
            movie_id=int(movie_id),
            action=SnapshotAction.FAVORITE,
            occurred_at=None,
        )
        for movie_id in favorite_movie_ids
    )

    saved_rows = db.execute(
        select(
            PlaylistMovie.movie_id,
            func.max(PlaylistMovie.created_at).label("saved_at"),
        )
        .join(Playlist, Playlist.id == PlaylistMovie.playlist_id)
        .join(Movie, Movie.id == PlaylistMovie.movie_id)
        .where(
            Playlist.user_id == user_id,
            PlaylistMovie.created_at <= cutoff_at,
            *eligible_catalog_movie_clause(),
        )
        .group_by(PlaylistMovie.movie_id)
    )
    signals.extend(
        SnapshotSignal(
            user_id=user_id,
            movie_id=int(movie_id),
            action=SnapshotAction.SAVED,
            occurred_at=normalize_datetime(saved_at),
        )
        for movie_id, saved_at in saved_rows
    )

    interaction_rows = db.execute(
        select(
            UserInteraction.movie_id,
            UserInteraction.is_pinned,
            UserInteraction.is_watched,
            UserInteraction.is_passed,
            UserInteraction.pinned_at,
            UserInteraction.watched_at,
            UserInteraction.passed_at,
        )
        .join(Movie, Movie.id == UserInteraction.movie_id)
        .where(
            UserInteraction.user_id == user_id,
            *eligible_catalog_movie_clause(),
        )
    )
    for row in interaction_rows:
        append_current_signal(
            signals,
            user_id=user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.PINNED,
            enabled=row.is_pinned,
            occurred_at=row.pinned_at,
            data_cutoff_at=cutoff_at,
        )
        append_current_signal(
            signals,
            user_id=user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.WATCHED,
            enabled=row.is_watched,
            occurred_at=row.watched_at,
            data_cutoff_at=cutoff_at,
        )
        append_current_signal(
            signals,
            user_id=user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.PASSED,
            enabled=row.is_passed,
            occurred_at=row.passed_at,
            data_cutoff_at=cutoff_at,
        )
    return tuple(sorted(signals, key=signal_sort_key))


def ref_id_sort_key(ref_id: str) -> tuple[int, int | str]:
    try:
        return 0, int(ref_id)
    except ValueError:
        return 1, ref_id
