from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from app.services.recsys.v3.config import CANDIDATE_POOL_SIZE, POLICY_BLOCKED_MOVIE_STATUSES
from app.services.recsys.v3.config import (
    POLICY_CATALOG_TRUST_PENALTY_MAX,
    POLICY_CATALOG_TRUST_VOTE_THRESHOLD,
    POLICY_NEGATIVE_MAX_ABSOLUTE,
    POLICY_NEGATIVE_MAX_BASE_RATIO,
    POLICY_ONTOLOGY_COMPONENT_WEIGHT,
    POLICY_ONTOLOGY_SHORT_TERM_MULTIPLIER,
    POLICY_PERSONAL_COMPONENT_WEIGHT,
)
from app.services.recsys.v3.retrieval.eligibility_schemas import HardFilterReason, HardFilterRejection
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.retrieval.retrieval_schemas import CandidateOntologyAnalysis, MergedCandidate


class RecommendationReasonType(StrEnum):
    ONTOLOGY_MATCH = "ontology_match"
    SUBSCRIBED_OTT = "subscribed_ott"
    QUALITY = "quality"
    RECENT_RELEASE = "recent_release"


@dataclass(frozen=True, slots=True)
class PolicyComponentWeights:
    personal: float = POLICY_PERSONAL_COMPONENT_WEIGHT
    ontology: float = POLICY_ONTOLOGY_COMPONENT_WEIGHT
    ontology_short_term_multiplier: float = POLICY_ONTOLOGY_SHORT_TERM_MULTIPLIER

    def __post_init__(self) -> None:
        for name, value in (
            ("personal", self.personal),
            ("ontology", self.ontology),
            ("ontology_short_term_multiplier", self.ontology_short_term_multiplier),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"policy component {name} must be between zero and one")
        if not math.isclose(self.personal + self.ontology, 1.0, abs_tol=1e-9):
            raise ValueError("policy personal and ontology weights must sum to one")


@dataclass(frozen=True, slots=True)
class PolicyAdjustmentSettings:
    catalog_trust_penalty_max: float = POLICY_CATALOG_TRUST_PENALTY_MAX
    catalog_trust_vote_threshold: int = POLICY_CATALOG_TRUST_VOTE_THRESHOLD
    negative_max_base_ratio: float = POLICY_NEGATIVE_MAX_BASE_RATIO
    negative_max_absolute: float = POLICY_NEGATIVE_MAX_ABSOLUTE

    def __post_init__(self) -> None:
        for name, value in (
            ("catalog_trust_penalty_max", self.catalog_trust_penalty_max),
            ("negative_max_base_ratio", self.negative_max_base_ratio),
            ("negative_max_absolute", self.negative_max_absolute),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"policy adjustment {name} must be between zero and one")
        if self.catalog_trust_vote_threshold <= 0:
            raise ValueError("catalog trust vote threshold must be positive")


@dataclass(frozen=True, slots=True)
class MoviePolicyMetadata:
    movie_id: int
    adult: bool
    title: str | None
    title_ko: str | None
    status: str | None
    popularity: float
    vote_average: float
    vote_count: int
    release_date: date | None

    def __post_init__(self) -> None:
        if self.movie_id <= 0:
            raise ValueError("movie policy metadata ID must be positive")
        for name, value in (("popularity", self.popularity), ("vote_average", self.vote_average)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"movie policy metadata {name} must be finite and non-negative")
        if self.vote_count < 0:
            raise ValueError("movie policy metadata vote count cannot be negative")


@dataclass(frozen=True, slots=True)
class PolicyRequestContext:
    as_of: datetime
    limit: int
    blacklisted_movie_ids: frozenset[int] = field(default_factory=frozenset)
    session_exposed_movie_ids: frozenset[int] = field(default_factory=frozenset)
    blocked_movie_ids: frozenset[int] = field(default_factory=frozenset)
    blocked_statuses: frozenset[str] = POLICY_BLOCKED_MOVIE_STATUSES
    genre_only_cold_start: bool = False

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.limit > CANDIDATE_POOL_SIZE:
            raise ValueError(
                f"policy result limit must be between 1 and {CANDIDATE_POOL_SIZE}"
            )
        for values in (
            self.blacklisted_movie_ids,
            self.session_exposed_movie_ids,
            self.blocked_movie_ids,
        ):
            if any(movie_id <= 0 for movie_id in values):
                raise ValueError("policy context movie IDs must be positive")
        if any(not status.strip() for status in self.blocked_statuses):
            raise ValueError("blocked statuses cannot be empty")


@dataclass(frozen=True, slots=True)
class RecommendationReason:
    reason_type: RecommendationReasonType
    value: float
    feature: FeatureName | None = None
    ref_ids: tuple[str, ...] = ()
    is_model_attribution: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or self.value < 0:
            raise ValueError("recommendation reason value must be finite and non-negative")
        if self.is_model_attribution:
            raise ValueError("ontology and policy reasons cannot be model attribution")


@dataclass(frozen=True, slots=True)
class PolicyScoreTrace:
    model_raw_score: float | None
    normalized_long_term_score: float
    long_term_ontology_raw_score: float | None
    normalized_long_term_ontology_score: float
    normalized_short_term_score: float
    cold_start_raw_score: float | None
    normalized_cold_start_score: float
    cold_start_overview_support_score: float
    cold_start_rule_selection_score: float | None
    cold_start_quality_score: float
    cold_start_genre_relevance_score: float
    cold_start_trusted_quality: bool
    candidate_selection_score: float
    ontology_raw_score: float
    normalized_ontology_score: float
    personal_component: float
    ontology_component: float
    base_score: float
    recency_adjustment: float
    ott_adjustment: float
    quality_adjustment: float
    catalog_trust_penalty: float
    negative_preference_penalty: float
    pre_rerank_score: float
    max_selected_similarity: float = 0.0
    repetition_penalty: float = 0.0
    mmr_similarity_penalty: float = 0.0
    short_term_lane_forced: bool = False
    final_score: float = 0.0

    def __post_init__(self) -> None:
        values = tuple(
            value
            for value in (
                self.normalized_long_term_score,
                self.normalized_long_term_ontology_score,
                self.normalized_short_term_score,
                self.normalized_cold_start_score,
                self.cold_start_overview_support_score,
                self.cold_start_quality_score,
                self.cold_start_genre_relevance_score,
                self.candidate_selection_score,
                self.ontology_raw_score,
                self.normalized_ontology_score,
                self.personal_component,
                self.ontology_component,
                self.base_score,
                self.recency_adjustment,
                self.ott_adjustment,
                self.quality_adjustment,
                self.catalog_trust_penalty,
                self.negative_preference_penalty,
                self.pre_rerank_score,
                self.max_selected_similarity,
                self.repetition_penalty,
                self.mmr_similarity_penalty,
                self.final_score,
            )
        )
        if self.model_raw_score is not None and not math.isfinite(self.model_raw_score):
            raise ValueError("policy model raw score must be finite")
        if (
            self.long_term_ontology_raw_score is not None
            and not math.isfinite(self.long_term_ontology_raw_score)
        ):
            raise ValueError("policy long-term ontology raw score must be finite")
        if self.cold_start_raw_score is not None and not math.isfinite(self.cold_start_raw_score):
            raise ValueError("policy cold-start raw score must be finite")
        if self.cold_start_rule_selection_score is not None and (
            not math.isfinite(self.cold_start_rule_selection_score)
            or not 0.0 <= self.cold_start_rule_selection_score <= 1.0
        ):
            raise ValueError("policy cold-start rule selection score must be between zero and one")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("policy score trace values must be finite")
        if any(value < 0 for value in values[:-1]):
            raise ValueError("policy score components must be non-negative")


@dataclass(frozen=True, slots=True)
class RankedPolicyCandidate:
    movie_id: int
    rank: int
    candidate: MergedCandidate
    ontology: CandidateOntologyAnalysis
    metadata: MoviePolicyMetadata
    score: PolicyScoreTrace
    reasons: tuple[RecommendationReason, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDiagnostics:
    input_candidate_count: int
    eligible_candidate_count: int
    rejected_candidate_count: int
    returned_candidate_count: int
    metadata_query_count: int
    elapsed_seconds: float
    short_term_lane_ratio: float = 0.0
    short_term_lane_target: int = 0
    input_short_term_only_count: int = 0
    eligible_short_term_only_count: int = 0
    selected_short_term_only_count: int = 0
    forced_short_term_only_count: int = 0
    unselected_short_term_only_count: int = 0


@dataclass(frozen=True, slots=True)
class PolicyEvaluationResult:
    candidates: tuple[RankedPolicyCandidate, ...]
    rejections: tuple[HardFilterRejection, ...]
    diagnostics: PolicyDiagnostics
