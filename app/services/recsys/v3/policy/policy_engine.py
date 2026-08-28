from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from sqlalchemy.orm import Session

from app.crud.recsys.movies import load_movies_by_ids
from app.services.recsys.v3.retrieval.candidate_eligibility import hard_filter_reasons, to_policy_metadata
from app.services.recsys.v3.config import (
    POLICY_MMR_SIMILARITY_PENALTY_MAX,
    POLICY_NEGATIVE_CONFIDENCE_PAIR_COUNT,
    POLICY_NEGATIVE_FEATURE_WEIGHTS,
    POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER,
    POLICY_OTT_BONUS_MAX,
    POLICY_QUALITY_BONUS_MAX,
    POLICY_RECENCY_BONUS_MAX,
    POLICY_RECENCY_WINDOW_DAYS,
    POLICY_REPETITION_PENALTY_MAX,
    POLICY_SHORT_TERM_MAX_RATIO,
    POLICY_SHORT_TERM_MIN_RATIO,
)
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.policy.policy_schemas import (
    HardFilterRejection,
    MoviePolicyMetadata,
    PolicyDiagnostics,
    PolicyEvaluationResult,
    PolicyAdjustmentSettings,
    PolicyComponentWeights,
    PolicyRequestContext,
    PolicyScoreTrace,
    RankedPolicyCandidate,
    RecommendationReason,
    RecommendationReasonType,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateFeatureSet,
    CandidateOntologyAnalysis,
    CandidateSource,
    MergedCandidate,
    RetrievalPipelineResult,
)
from app.services.recsys.v3.policy.quality import reliable_quality_score
from app.services.recsys.v3.domain.schemas import (
    OttFilterMode,
    ShortTermPreferenceState,
    UserProfileBundle,
)
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize


_REPETITION_FAMILIES = ("genre", "actor", "director", "theme", "mood")


def evaluate_policy_candidates(
    db: Session,
    *,
    retrieval: RetrievalPipelineResult,
    profile: UserProfileBundle,
    context: PolicyRequestContext,
    component_weights: PolicyComponentWeights | None = None,
    adjustment_settings: PolicyAdjustmentSettings | None = None,
) -> PolicyEvaluationResult:
    return evaluate_candidate_set(
        db,
        candidates=retrieval.merged.candidates,
        analyses=retrieval.ontology.candidates,
        profile=profile,
        context=context,
        component_weights=component_weights,
        adjustment_settings=adjustment_settings,
        prior_rejections=retrieval.prefilter_rejections,
        input_candidate_count=(
            retrieval.eligibility.input_candidate_count or len(retrieval.merged.candidates)
        ),
    )


def evaluate_candidate_set(
    db: Session,
    *,
    candidates: Sequence[MergedCandidate],
    analyses: Sequence[CandidateOntologyAnalysis],
    profile: UserProfileBundle,
    context: PolicyRequestContext,
    prior_rejections: Sequence[HardFilterRejection] = (),
    input_candidate_count: int | None = None,
    component_weights: PolicyComponentWeights | None = None,
    adjustment_settings: PolicyAdjustmentSettings | None = None,
) -> PolicyEvaluationResult:
    started = time.monotonic()
    if tuple(item.movie_id for item in candidates) != tuple(item.movie_id for item in analyses):
        raise ValueError("policy candidate and ontology order must match")

    metadata = {
        item.movie_id: item
        for item in map(
            to_policy_metadata,
            load_movies_by_ids(db, [candidate.movie_id for candidate in candidates]),
        )
    }
    eligible: list[tuple[MergedCandidate, CandidateOntologyAnalysis, MoviePolicyMetadata]] = []
    rejections: list[HardFilterRejection] = list(prior_rejections)
    for candidate, analysis in zip(candidates, analyses, strict=True):
        movie = metadata.get(candidate.movie_id)
        reasons = hard_filter_reasons(
            candidate.movie_id,
            metadata=movie,
            on_subscribed_ott=bool(analysis.ott.subscribed_streaming_ott_ids),
            profile=profile,
            context=context,
        )
        if reasons:
            rejections.append(HardFilterRejection(movie_id=candidate.movie_id, reasons=reasons))
        elif movie is not None:
            eligible.append((candidate, analysis, movie))

    weights = component_weights or PolicyComponentWeights()
    adjustments = adjustment_settings or PolicyAdjustmentSettings()
    ontology_raw = {
        candidate.movie_id: analysis.long_positive_total
        + weights.ontology_short_term_multiplier * analysis.short_positive_total
        for candidate, analysis, _movie in eligible
    }
    normalized_ontology = _normalize_ontology_scores(ontology_raw)
    scored = tuple(
        _score_candidate(
            candidate,
            analysis,
            movie,
            normalized_ontology_score=normalized_ontology[candidate.movie_id],
            profile=profile,
            as_of=context.as_of.date(),
            component_weights=weights,
            adjustment_settings=adjustments,
        )
        for candidate, analysis, movie in eligible
    )
    short_term_lane_ratio = _short_term_lane_ratio(profile)
    short_term_lane_target = _short_term_lane_target(
        scored,
        limit=min(context.limit, len(scored)),
        profile=profile,
    )
    ranked = _deterministic_rerank(
        scored,
        limit=min(context.limit, len(scored)),
        profile=profile,
    )
    return PolicyEvaluationResult(
        candidates=ranked,
        rejections=tuple(rejections),
        diagnostics=PolicyDiagnostics(
            input_candidate_count=(
                input_candidate_count if input_candidate_count is not None else len(candidates)
            ),
            eligible_candidate_count=len(eligible),
            rejected_candidate_count=len(rejections),
            returned_candidate_count=len(ranked),
            metadata_query_count=int(bool(candidates)),
            elapsed_seconds=round(time.monotonic() - started, 6),
            short_term_lane_ratio=round(short_term_lane_ratio, 8),
            short_term_lane_target=short_term_lane_target,
            input_short_term_only_count=sum(
                _is_short_term_only_candidate(item) for item in candidates
            ),
            eligible_short_term_only_count=sum(
                _is_short_term_only_candidate(item) for item in scored
            ),
            selected_short_term_only_count=sum(
                _is_short_term_only_candidate(item) for item in ranked
            ),
            forced_short_term_only_count=sum(
                item.score.short_term_lane_forced for item in ranked
            ),
            unselected_short_term_only_count=max(
                0,
                sum(_is_short_term_only_candidate(item) for item in scored)
                - sum(_is_short_term_only_candidate(item) for item in ranked),
            ),
        ),
    )


def _score_candidate(
    candidate: MergedCandidate,
    analysis: CandidateOntologyAnalysis,
    metadata: MoviePolicyMetadata,
    *,
    normalized_ontology_score: float,
    profile: UserProfileBundle,
    as_of: date,
    component_weights: PolicyComponentWeights,
    adjustment_settings: PolicyAdjustmentSettings,
) -> RankedPolicyCandidate:
    personal_component = component_weights.personal * candidate.candidate_selection_score
    ontology_raw = analysis.long_positive_total + (
        component_weights.ontology_short_term_multiplier * analysis.short_positive_total
    )
    ontology_component = component_weights.ontology * normalized_ontology_score
    base_score = personal_component + ontology_component
    recency = _recency_adjustment(metadata.release_date, as_of)
    ott = (
        POLICY_OTT_BONUS_MAX
        if profile.serving_context.ott_mode == OttFilterMode.ALL
        and analysis.ott.subscribed_streaming_ott_ids
        else 0.0
    )
    quality = _quality_adjustment(metadata)
    catalog_trust = _catalog_trust_penalty(metadata, settings=adjustment_settings)
    negative = _negative_penalty(
        analysis,
        profile=profile,
        base_score=base_score,
        settings=adjustment_settings,
    )
    pre_rerank = max(
        0.0,
        base_score + recency + ott + quality - catalog_trust - negative,
    )
    trace = PolicyScoreTrace(
        model_raw_score=candidate.model_raw_score,
        normalized_long_term_score=candidate.normalized_long_term_score,
        long_term_ontology_raw_score=candidate.long_term_ontology_raw_score,
        normalized_long_term_ontology_score=(
            candidate.normalized_long_term_ontology_score
        ),
        normalized_short_term_score=candidate.normalized_short_term_score,
        cold_start_raw_score=candidate.cold_start_raw_score,
        normalized_cold_start_score=candidate.normalized_cold_start_score,
        cold_start_overview_support_score=(
            candidate.cold_start_overview_support_score
        ),
        cold_start_rule_selection_score=(
            candidate.cold_start_rule_selection_score
        ),
        cold_start_quality_score=candidate.cold_start_quality_score,
        cold_start_genre_relevance_score=(
            candidate.cold_start_genre_relevance_score
        ),
        cold_start_trusted_quality=candidate.cold_start_trusted_quality,
        candidate_selection_score=candidate.candidate_selection_score,
        ontology_raw_score=round(ontology_raw, 8),
        normalized_ontology_score=normalized_ontology_score,
        personal_component=round(personal_component, 8),
        ontology_component=round(ontology_component, 8),
        base_score=round(base_score, 8),
        recency_adjustment=recency,
        ott_adjustment=ott,
        quality_adjustment=quality,
        catalog_trust_penalty=catalog_trust,
        negative_preference_penalty=negative,
        pre_rerank_score=round(pre_rerank, 8),
        final_score=round(pre_rerank, 8),
    )
    return RankedPolicyCandidate(
        movie_id=candidate.movie_id,
        rank=0,
        candidate=candidate,
        ontology=analysis,
        metadata=metadata,
        score=trace,
        reasons=_build_reasons(analysis, trace),
    )


def _negative_penalty(
    analysis: CandidateOntologyAnalysis,
    *,
    profile: UserProfileBundle,
    base_score: float,
    settings: PolicyAdjustmentSettings | None = None,
) -> float:
    resolved = settings or PolicyAdjustmentSettings()
    weighted_raw = 0.0
    for type_score in analysis.type_scores:
        weight = POLICY_NEGATIVE_FEATURE_WEIGHTS[type_score.feature.value]
        weighted_raw += weight * (
            type_score.long_negative_score
            + POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER * type_score.short_negative_score
        )
    evidence_confidence = min(
        1.0,
        profile.long_term.passed_pair_count / POLICY_NEGATIVE_CONFIDENCE_PAIR_COUNT,
    )
    cap = min(
        resolved.negative_max_absolute,
        base_score * resolved.negative_max_base_ratio,
    )
    return round(cap * evidence_confidence * (1.0 - math.exp(-weighted_raw)), 8)


def _quality_adjustment(metadata: MoviePolicyMetadata) -> float:
    return round(
        POLICY_QUALITY_BONUS_MAX
        * reliable_quality_score(
            popularity=metadata.popularity,
            vote_average=metadata.vote_average,
            vote_count=metadata.vote_count,
        ),
        8,
    )


def _catalog_trust_penalty(
    metadata: MoviePolicyMetadata,
    *,
    settings: PolicyAdjustmentSettings | None = None,
) -> float:
    resolved = settings or PolicyAdjustmentSettings()
    if metadata.vote_count >= resolved.catalog_trust_vote_threshold:
        return 0.0
    evidence_gap = (
        resolved.catalog_trust_vote_threshold - metadata.vote_count
    ) / resolved.catalog_trust_vote_threshold
    return round(resolved.catalog_trust_penalty_max * evidence_gap, 8)


def _recency_adjustment(release_date: date | None, as_of: date) -> float:
    if release_date is None or release_date > as_of:
        return 0.0
    age_days = (as_of - release_date).days
    if age_days >= POLICY_RECENCY_WINDOW_DAYS:
        return 0.0
    return round(
        POLICY_RECENCY_BONUS_MAX * (1.0 - age_days / POLICY_RECENCY_WINDOW_DAYS),
        8,
    )


def _deterministic_rerank(
    candidates: tuple[RankedPolicyCandidate, ...],
    *,
    limit: int,
    profile: UserProfileBundle,
) -> tuple[RankedPolicyCandidate, ...]:
    remaining = list(candidates)
    selected: list[RankedPolicyCandidate] = []
    feature_counts = {family: Counter() for family in _REPETITION_FAMILIES}
    short_term_ratio = _short_term_lane_ratio(profile)
    short_term_available = sum(_is_short_term_only_candidate(item) for item in candidates)
    while remaining and len(selected) < limit:
        scored_options: list[tuple[float, float, float, RankedPolicyCandidate]] = []
        for candidate in remaining:
            max_similarity = max(
                (_candidate_similarity(candidate.ontology.repetition_features, item.ontology.repetition_features)
                 for item in selected),
                default=0.0,
            )
            repetition = _repetition_penalty(candidate.ontology.repetition_features, feature_counts)
            similarity_penalty = POLICY_MMR_SIMILARITY_PENALTY_MAX * max_similarity
            final_score = max(
                0.0,
                candidate.score.pre_rerank_score - repetition - similarity_penalty,
            )
            scored_options.append((final_score, max_similarity, repetition, candidate))
        short_term_selected = sum(_is_short_term_only_candidate(item) for item in selected)
        required_at_position = min(
            short_term_available,
            math.floor((len(selected) + 1) * short_term_ratio + 0.5),
        )
        force_short_term = (
            short_term_selected < required_at_position
            and any(_is_short_term_only_candidate(item) for item in remaining)
        )
        options = (
            [item for item in scored_options if _is_short_term_only_candidate(item[3])]
            if force_short_term
            else scored_options
        )
        final_score, max_similarity, repetition, chosen = min(
            options,
            key=lambda item: (
                -item[0],
                item[3].candidate.selection_rank,
                item[3].movie_id,
            ),
        )
        similarity_penalty = POLICY_MMR_SIMILARITY_PENALTY_MAX * max_similarity
        original = chosen
        chosen = replace(
            original,
            rank=len(selected) + 1,
            score=replace(
                chosen.score,
                max_selected_similarity=round(max_similarity, 8),
                repetition_penalty=round(repetition, 8),
                mmr_similarity_penalty=round(similarity_penalty, 8),
                short_term_lane_forced=force_short_term,
                final_score=round(final_score, 8),
            ),
        )
        selected.append(chosen)
        remaining.remove(original)
        _update_feature_counts(chosen.ontology.repetition_features, feature_counts)
    return tuple(selected)


def _short_term_lane_target(
    candidates: tuple[RankedPolicyCandidate, ...],
    *,
    limit: int,
    profile: UserProfileBundle,
) -> int:
    ratio = _short_term_lane_ratio(profile)
    requested = math.floor(limit * ratio + 0.5)
    available = sum(_is_short_term_only_candidate(item) for item in candidates)
    return min(requested, available)


def _short_term_lane_ratio(profile: UserProfileBundle) -> float:
    if profile.short_term.preference_state != ShortTermPreferenceState.DRIFT:
        return 0.0
    return POLICY_SHORT_TERM_MIN_RATIO + (
        POLICY_SHORT_TERM_MAX_RATIO - POLICY_SHORT_TERM_MIN_RATIO
    ) * profile.short_term.drift_confidence


def _is_short_term_only_candidate(
    candidate: RankedPolicyCandidate | MergedCandidate,
) -> bool:
    merged = candidate.candidate if isinstance(candidate, RankedPolicyCandidate) else candidate
    return merged.sources == (CandidateSource.SHORT_TERM_CONTEXT,)


def _candidate_similarity(left: CandidateFeatureSet, right: CandidateFeatureSet) -> float:
    similarities: list[float] = []
    for family in _REPETITION_FAMILIES:
        left_values = getattr(left, family)
        right_values = getattr(right, family)
        union = left_values | right_values
        if union:
            similarities.append(len(left_values & right_values) / len(union))
    return sum(similarities) / len(similarities) if similarities else 0.0


def _repetition_penalty(
    features: CandidateFeatureSet,
    feature_counts: dict[str, Counter],
) -> float:
    family_pressure: list[int] = []
    for family in _REPETITION_FAMILIES:
        values = getattr(features, family)
        if values:
            family_pressure.append(max(feature_counts[family][value] for value in values))
    if not family_pressure:
        return 0.0
    pressure = sum(family_pressure) / len(family_pressure)
    return POLICY_REPETITION_PENALTY_MAX * (1.0 - math.exp(-pressure))


def _update_feature_counts(features: CandidateFeatureSet, counts: dict[str, Counter]) -> None:
    for family in _REPETITION_FAMILIES:
        counts[family].update(getattr(features, family))


def _build_reasons(
    analysis: CandidateOntologyAnalysis,
    trace: PolicyScoreTrace,
) -> tuple[RecommendationReason, ...]:
    reasons = [
        RecommendationReason(
            reason_type=RecommendationReasonType.ONTOLOGY_MATCH,
            feature=item.feature,
            value=round(item.long_positive_score + item.short_positive_score, 8),
        )
        for item in sorted(
            analysis.type_scores,
            key=lambda value: (
                -(value.long_positive_score + value.short_positive_score),
                value.feature.value,
            ),
        )
        if item.long_positive_score + item.short_positive_score > 0
    ][:3]
    if trace.ott_adjustment > 0:
        reasons.append(
            RecommendationReason(
                reason_type=RecommendationReasonType.SUBSCRIBED_OTT,
                value=trace.ott_adjustment,
                ref_ids=tuple(str(value) for value in sorted(analysis.ott.subscribed_streaming_ott_ids)),
            )
        )
    if trace.quality_adjustment >= POLICY_QUALITY_BONUS_MAX * 0.5:
        reasons.append(
            RecommendationReason(
                reason_type=RecommendationReasonType.QUALITY,
                value=trace.quality_adjustment,
            )
        )
    if trace.recency_adjustment > 0:
        reasons.append(
            RecommendationReason(
                reason_type=RecommendationReasonType.RECENT_RELEASE,
                value=trace.recency_adjustment,
            )
        )
    return tuple(reasons)


def _normalize_ontology_scores(scores: dict[int, float]) -> dict[int, float]:
    if scores and all(score == 0.0 for score in scores.values()):
        return {movie_id: 0.0 for movie_id in scores}
    return percentile_normalize(scores)
