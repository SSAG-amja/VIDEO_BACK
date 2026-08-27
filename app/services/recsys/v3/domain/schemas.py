from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.services.recsys.v3.domain.feature_registry import (
    ConsumerStatus,
    FeatureConsumer,
    FeatureName,
    get_feature_definition,
)


class ProfileMaturity(StrEnum):
    NO_PROFILE = "no_profile"
    ONBOARDING_ONLY = "onboarding_only"
    SPARSE = "sparse"
    LIGHT = "light"
    ESTABLISHED = "established"


class FeatureDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class OttFilterMode(StrEnum):
    ALL = "all"
    SUBSCRIBED_ONLY = "subscribed_only"


@dataclass(frozen=True, slots=True)
class ProfileFeatureEvidence:
    ontology_build_id: int
    edge_id: int
    relation_type: str
    source_movie_id: int
    action: str
    direction: FeatureDirection
    occurred_at: datetime | None
    action_strength: float
    recency_multiplier: float
    edge_strength: float
    family_normalizer: float
    contribution: float

    def __post_init__(self) -> None:
        if self.ontology_build_id <= 0 or self.edge_id <= 0 or self.source_movie_id <= 0:
            raise ValueError("profile evidence IDs must be positive")
        if not self.relation_type.strip() or not self.action.strip():
            raise ValueError("profile evidence relation and action are required")
        for name, value in (
            ("action_strength", self.action_strength),
            ("recency_multiplier", self.recency_multiplier),
            ("edge_strength", self.edge_strength),
            ("family_normalizer", self.family_normalizer),
            ("contribution", self.contribution),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"profile evidence {name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ProfileFeatureSignal:
    feature: FeatureName
    ref_id: str
    direction: FeatureDirection
    score: float
    source_movie_ids: frozenset[int] = field(default_factory=frozenset)
    source_actions: tuple[str, ...] = ()
    latest_at: datetime | None = None
    raw_score: float | None = None
    contribution_count: int = 0
    evidence: tuple[ProfileFeatureEvidence, ...] = ()

    def __post_init__(self) -> None:
        normalized_ref_id = self.ref_id.strip()
        if not normalized_ref_id:
            raise ValueError("profile feature ref_id cannot be empty")
        if normalized_ref_id != self.ref_id:
            object.__setattr__(self, "ref_id", normalized_ref_id)
        if not math.isfinite(self.score) or self.score < 0:
            raise ValueError("profile feature score must be finite and non-negative")
        if self.raw_score is not None and (
            not math.isfinite(self.raw_score) or self.raw_score < self.score
        ):
            raise ValueError("profile feature raw score must be finite and at least the capped score")
        if self.contribution_count < len(self.evidence):
            raise ValueError("profile feature contribution count cannot be smaller than evidence")
        if any(movie_id <= 0 for movie_id in self.source_movie_ids):
            raise ValueError("profile source movie IDs must be positive")
        if any(item.direction != self.direction for item in self.evidence):
            raise ValueError("profile feature evidence direction mismatch")
        if self.evidence:
            evidence_movie_ids = frozenset(item.source_movie_id for item in self.evidence)
            evidence_actions = {item.action for item in self.evidence}
            if not evidence_movie_ids <= self.source_movie_ids:
                raise ValueError("profile feature evidence movie is missing from source IDs")
            if not evidence_actions <= set(self.source_actions):
                raise ValueError("profile feature evidence action is missing from source actions")


@dataclass(frozen=True, slots=True)
class ProfileFamilyDiagnostics:
    feature: FeatureName
    direction: FeatureDirection
    source_edge_count: int
    source_value_count: int
    retained_value_count: int
    dropped_value_count: int
    top_k: int
    score_cap: float

    def __post_init__(self) -> None:
        validate_non_negative_counts(
            source_edge_count=self.source_edge_count,
            source_value_count=self.source_value_count,
            retained_value_count=self.retained_value_count,
            dropped_value_count=self.dropped_value_count,
            top_k=self.top_k,
        )
        if self.retained_value_count + self.dropped_value_count != self.source_value_count:
            raise ValueError("profile retained and dropped values must equal source values")
        if not math.isfinite(self.score_cap) or self.score_cap <= 0:
            raise ValueError("profile score cap must be finite and positive")


@dataclass(frozen=True, slots=True)
class RuntimeProfileDiagnostics:
    ontology_build_id: int
    elapsed_seconds: float
    source_action_count: int
    graph_source_movie_count: int
    graph_covered_movie_count: int
    feature_uncovered_movie_ids: tuple[int, ...] = ()
    long_term_families: tuple[ProfileFamilyDiagnostics, ...] = ()
    short_term_families: tuple[ProfileFamilyDiagnostics, ...] = ()
    drift_components: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ontology_build_id <= 0:
            raise ValueError("runtime profile ontology build ID must be positive")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("runtime profile elapsed time must be finite and non-negative")
        validate_non_negative_counts(
            source_action_count=self.source_action_count,
            graph_source_movie_count=self.graph_source_movie_count,
            graph_covered_movie_count=self.graph_covered_movie_count,
        )
        if self.graph_covered_movie_count > self.graph_source_movie_count:
            raise ValueError("graph coverage cannot exceed source movie count")
        validate_positive_ids(
            frozenset(self.feature_uncovered_movie_ids),
            "feature-uncovered movie",
        )


@dataclass(frozen=True, slots=True)
class OnboardingProfile:
    user_id: int
    favorite_movie_ids: frozenset[int] = field(default_factory=frozenset)
    genre_ids: frozenset[int] = field(default_factory=frozenset)
    derived_feature_priors: tuple[ProfileFeatureSignal, ...] = ()

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)
        validate_positive_ids(self.favorite_movie_ids, "favorite movie")
        validate_positive_ids(self.genre_ids, "genre")
        validate_profile_features(
            self.derived_feature_priors,
            consumer=FeatureConsumer.ONBOARDING_PROFILE,
            expected_direction=FeatureDirection.POSITIVE,
        )


@dataclass(frozen=True, slots=True)
class LongTermProfile:
    user_id: int
    as_of: datetime
    maturity: ProfileMaturity
    model_user_known: bool
    positive_movie_ids: frozenset[int] = field(default_factory=frozenset)
    negative_movie_ids: frozenset[int] = field(default_factory=frozenset)
    excluded_movie_ids: frozenset[int] = field(default_factory=frozenset)
    positive_features: tuple[ProfileFeatureSignal, ...] = ()
    negative_features: tuple[ProfileFeatureSignal, ...] = ()
    positive_pair_count: int = 0
    passed_pair_count: int = 0
    watched_pair_count: int = 0

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)
        validate_positive_ids(self.positive_movie_ids, "positive movie")
        validate_positive_ids(self.negative_movie_ids, "negative movie")
        validate_positive_ids(self.excluded_movie_ids, "excluded movie")
        if self.positive_movie_ids & self.negative_movie_ids:
            raise ValueError("long-term positive and negative movie IDs must be disjoint")
        if not self.negative_movie_ids <= self.excluded_movie_ids:
            raise ValueError("long-term negative movie IDs must be included in exclusions")
        validate_non_negative_counts(
            positive_pair_count=self.positive_pair_count,
            passed_pair_count=self.passed_pair_count,
            watched_pair_count=self.watched_pair_count,
        )
        validate_profile_features(
            self.positive_features,
            consumer=FeatureConsumer.LONG_TERM_PROFILE,
            expected_direction=FeatureDirection.POSITIVE,
        )
        validate_profile_features(
            self.negative_features,
            consumer=FeatureConsumer.LONG_TERM_PROFILE,
            expected_direction=FeatureDirection.NEGATIVE,
        )


@dataclass(frozen=True, slots=True)
class ShortTermProfile:
    user_id: int
    as_of: datetime
    window_action_count: int
    drift_confidence: float = 0.0
    recent_positive_movie_ids: frozenset[int] = field(default_factory=frozenset)
    recent_negative_movie_ids: frozenset[int] = field(default_factory=frozenset)
    positive_features: tuple[ProfileFeatureSignal, ...] = ()
    negative_features: tuple[ProfileFeatureSignal, ...] = ()

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)
        if self.window_action_count < 0:
            raise ValueError("short-term window action count cannot be negative")
        if not math.isfinite(self.drift_confidence) or not 0.0 <= self.drift_confidence <= 1.0:
            raise ValueError("drift confidence must be between 0 and 1")
        validate_positive_ids(self.recent_positive_movie_ids, "recent positive movie")
        validate_positive_ids(self.recent_negative_movie_ids, "recent negative movie")
        if self.recent_positive_movie_ids & self.recent_negative_movie_ids:
            raise ValueError("short-term positive and negative movie IDs must be disjoint")
        validate_profile_features(
            self.positive_features,
            consumer=FeatureConsumer.SHORT_TERM_PROFILE,
            expected_direction=FeatureDirection.POSITIVE,
        )
        validate_profile_features(
            self.negative_features,
            consumer=FeatureConsumer.SHORT_TERM_PROFILE,
            expected_direction=FeatureDirection.NEGATIVE,
        )


@dataclass(frozen=True, slots=True)
class ServingContext:
    user_id: int
    ott_mode: OttFilterMode
    availability_as_of: datetime
    subscribed_ott_ids: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)
        validate_positive_ids(self.subscribed_ott_ids, "subscribed OTT")


@dataclass(frozen=True, slots=True)
class UserProfileBundle:
    user_id: int
    onboarding: OnboardingProfile
    long_term: LongTermProfile
    short_term: ShortTermProfile
    serving_context: ServingContext

    def __post_init__(self) -> None:
        validate_user_id(self.user_id)
        profile_user_ids = {
            self.onboarding.user_id,
            self.long_term.user_id,
            self.short_term.user_id,
            self.serving_context.user_id,
        }
        if profile_user_ids != {self.user_id}:
            raise ValueError("profile bundle contains mismatched user IDs")


@dataclass(frozen=True, slots=True)
class RuntimeProfileBuildResult:
    bundle: UserProfileBundle
    diagnostics: RuntimeProfileDiagnostics


@dataclass(frozen=True, slots=True)
class FeatureDropCount:
    reason: str
    count: int

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("feature drop reason cannot be empty")
        if self.count < 0:
            raise ValueError("feature drop count cannot be negative")


@dataclass(frozen=True, slots=True)
class FeatureCoverageDiagnostics:
    feature: FeatureName
    consumer: FeatureConsumer
    total_entity_count: int
    covered_entity_count: int
    source_value_count: int
    retained_value_count: int
    dropped_value_count: int
    drop_counts: tuple[FeatureDropCount, ...] = ()

    def __post_init__(self) -> None:
        validate_non_negative_counts(
            total_entity_count=self.total_entity_count,
            covered_entity_count=self.covered_entity_count,
            source_value_count=self.source_value_count,
            retained_value_count=self.retained_value_count,
            dropped_value_count=self.dropped_value_count,
        )
        if self.covered_entity_count > self.total_entity_count:
            raise ValueError("covered entity count cannot exceed total entity count")
        if self.retained_value_count + self.dropped_value_count != self.source_value_count:
            raise ValueError("retained and dropped feature counts must equal source count")
        if sum(item.count for item in self.drop_counts) != self.dropped_value_count:
            raise ValueError("feature drop reasons must sum to dropped value count")


def validate_profile_features(
    features: tuple[ProfileFeatureSignal, ...],
    *,
    consumer: FeatureConsumer,
    expected_direction: FeatureDirection | None = None,
) -> None:
    seen: set[tuple[FeatureName, str, FeatureDirection]] = set()
    for feature in features:
        if expected_direction is not None and feature.direction != expected_direction:
            raise ValueError(
                f"profile feature direction mismatch expected={expected_direction.value}"
            )
        key = (feature.feature, feature.ref_id, feature.direction)
        if key in seen:
            raise ValueError(
                f"duplicate profile feature feature={feature.feature.value} ref_id={feature.ref_id}"
            )
        seen.add(key)

        definition = get_feature_definition(feature.feature)
        if definition.consumer_status(consumer) == ConsumerStatus.DISABLED:
            raise ValueError(
                f"feature is disabled for profile consumer feature={feature.feature.value} "
                f"consumer={consumer.value}"
            )


def validate_user_id(user_id: int) -> None:
    if user_id <= 0:
        raise ValueError("user ID must be positive")


def validate_positive_ids(values: frozenset[int], label: str) -> None:
    if any(value <= 0 for value in values):
        raise ValueError(f"{label} IDs must be positive")


def validate_non_negative_counts(**counts: int) -> None:
    for name, count in counts.items():
        if count < 0:
            raise ValueError(f"{name} cannot be negative")
