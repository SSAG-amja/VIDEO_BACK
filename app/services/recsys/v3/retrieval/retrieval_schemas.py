from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from app.services.recsys.v3.retrieval.eligibility_schemas import (
    CandidateEligibilityDiagnostics,
    HardFilterRejection,
)
from app.services.recsys.v3.domain.feature_registry import FeatureName


class CandidateSource(StrEnum):
    MODEL = "model"
    FEATURE_ONLY_MODEL = "feature_only_model"
    LONG_TERM_ONTOLOGY = "long_term_ontology"
    SHORT_TERM_CONTEXT = "short_term_context"
    COLD_START = "cold_start"
    ONTOLOGY_COLD_ITEM = "ontology_cold_item"


class ColdStartStrategy(StrEnum):
    ONTOLOGY_RULE = "ontology_rule"
    QUALITY_FALLBACK = "quality_fallback"


class ProfileScope(StrEnum):
    LONG_TERM = "long_term"
    SHORT_TERM = "short_term"


@dataclass(frozen=True, slots=True)
class LongTermCandidate:
    movie_id: int
    model_raw_score: float
    source_rank: int

    def __post_init__(self) -> None:
        _validate_candidate(self.movie_id, self.model_raw_score, self.source_rank)


@dataclass(frozen=True, slots=True)
class LongTermOntologyCandidate:
    movie_id: int
    ontology_raw_score: float
    source_rank: int

    def __post_init__(self) -> None:
        _validate_candidate(self.movie_id, self.ontology_raw_score, self.source_rank)
        if self.ontology_raw_score < 0:
            raise ValueError("long-term ontology candidate score cannot be negative")


@dataclass(frozen=True, slots=True)
class ShortTermCandidate:
    movie_id: int
    short_term_raw_score: float
    source_rank: int

    def __post_init__(self) -> None:
        _validate_candidate(self.movie_id, self.short_term_raw_score, self.source_rank)
        if self.short_term_raw_score < 0:
            raise ValueError("short-term candidate score cannot be negative")


@dataclass(frozen=True, slots=True)
class ColdStartCandidate:
    movie_id: int
    raw_score: float
    source_rank: int
    source: CandidateSource
    overview_support_score: float = 0.0
    rule_selection_score: float | None = None
    quality_score: float = 0.0
    genre_relevance_score: float = 0.0
    trusted_quality: bool = False

    def __post_init__(self) -> None:
        _validate_candidate(self.movie_id, self.raw_score, self.source_rank)
        if self.raw_score < 0:
            raise ValueError("cold-start candidate score cannot be negative")
        if not math.isfinite(self.overview_support_score) or self.overview_support_score < 0:
            raise ValueError("cold-start overview support score must be finite and non-negative")
        if self.rule_selection_score is not None and (
            not math.isfinite(self.rule_selection_score)
            or not 0.0 <= self.rule_selection_score <= 1.0
        ):
            raise ValueError("cold-start rule selection score must be between zero and one")
        for name, value in (
            ("quality_score", self.quality_score),
            ("genre_relevance_score", self.genre_relevance_score),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"cold-start {name} must be between zero and one")
        if self.source not in {CandidateSource.COLD_START, CandidateSource.ONTOLOGY_COLD_ITEM}:
            raise ValueError("invalid cold-start candidate source")


@dataclass(frozen=True, slots=True)
class MergedCandidate:
    movie_id: int
    sources: tuple[CandidateSource, ...]
    selection_rank: int
    candidate_selection_score: float
    model_raw_score: float | None = None
    normalized_long_term_score: float = 0.0
    model_source_rank: int | None = None
    long_term_ontology_raw_score: float | None = None
    normalized_long_term_ontology_score: float = 0.0
    long_term_ontology_source_rank: int | None = None
    short_term_raw_score: float | None = None
    normalized_short_term_score: float = 0.0
    short_term_source_rank: int | None = None
    cold_start_raw_score: float | None = None
    normalized_cold_start_score: float = 0.0
    cold_start_source_rank: int | None = None
    cold_start_overview_support_score: float = 0.0
    cold_start_rule_selection_score: float | None = None
    cold_start_quality_score: float = 0.0
    cold_start_genre_relevance_score: float = 0.0
    cold_start_trusted_quality: bool = False

    def __post_init__(self) -> None:
        if self.movie_id <= 0 or self.selection_rank <= 0:
            raise ValueError("merged candidate IDs and rank must be positive")
        if not self.sources or len(set(self.sources)) != len(self.sources):
            raise ValueError("merged candidate sources must be non-empty and unique")
        for name, value in (
            ("candidate_selection_score", self.candidate_selection_score),
            ("normalized_long_term_score", self.normalized_long_term_score),
            (
                "normalized_long_term_ontology_score",
                self.normalized_long_term_ontology_score,
            ),
            ("normalized_short_term_score", self.normalized_short_term_score),
            ("normalized_cold_start_score", self.normalized_cold_start_score),
            ("cold_start_overview_support_score", self.cold_start_overview_support_score),
            ("cold_start_quality_score", self.cold_start_quality_score),
            ("cold_start_genre_relevance_score", self.cold_start_genre_relevance_score),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        model_sources = {CandidateSource.MODEL, CandidateSource.FEATURE_ONLY_MODEL}
        has_model_source = bool(model_sources & set(self.sources))
        if len(model_sources & set(self.sources)) > 1:
            raise ValueError("candidate cannot have multiple model sources")
        if has_model_source != (self.model_raw_score is not None):
            raise ValueError("model source and raw score must agree")
        if (self.short_term_raw_score is None) != (
            CandidateSource.SHORT_TERM_CONTEXT not in self.sources
        ):
            raise ValueError("short-term source and raw score must agree")
        if has_model_source != (self.model_source_rank is not None):
            raise ValueError("model source and rank must agree")
        has_long_term_ontology = CandidateSource.LONG_TERM_ONTOLOGY in self.sources
        if has_long_term_ontology != (self.long_term_ontology_raw_score is not None):
            raise ValueError("long-term ontology source and raw score must agree")
        if has_long_term_ontology != (self.long_term_ontology_source_rank is not None):
            raise ValueError("long-term ontology source and rank must agree")
        if (self.short_term_source_rank is None) != (
            CandidateSource.SHORT_TERM_CONTEXT not in self.sources
        ):
            raise ValueError("short-term source and rank must agree")
        cold_sources = {CandidateSource.COLD_START, CandidateSource.ONTOLOGY_COLD_ITEM}
        has_cold_source = bool(cold_sources & set(self.sources))
        if len(cold_sources & set(self.sources)) > 1:
            raise ValueError("candidate cannot have multiple cold-start rule sources")
        if has_cold_source != (self.cold_start_raw_score is not None):
            raise ValueError("cold-start source and raw score must agree")
        if has_cold_source != (self.cold_start_source_rank is not None):
            raise ValueError("cold-start source and rank must agree")
        if not has_cold_source and self.cold_start_overview_support_score != 0.0:
            raise ValueError("overview support requires a cold-start source")
        if self.cold_start_rule_selection_score is not None and (
            not has_cold_source
            or not math.isfinite(self.cold_start_rule_selection_score)
            or not 0.0 <= self.cold_start_rule_selection_score <= 1.0
        ):
            raise ValueError("cold-start rule selection score requires a cold-start source")
        if not has_cold_source and (
            self.cold_start_quality_score != 0.0
            or self.cold_start_genre_relevance_score != 0.0
            or self.cold_start_trusted_quality
        ):
            raise ValueError("cold-start quality diagnostics require a cold-start source")


@dataclass(frozen=True, slots=True)
class CandidateMergeDiagnostics:
    long_term_source_count: int
    short_term_source_count: int
    raw_union_count: int
    selected_count: int
    drift_confidence: float
    drift_weight: float
    contextual_floor_count: int
    selected_model_only_count: int = 0
    selected_short_only_count: int = 0
    selected_overlap_count: int = 0
    long_term_ontology_source_count: int = 0
    long_term_ontology_floor_count: int = 0
    selected_long_term_ontology_count: int = 0
    selected_long_term_ontology_only_count: int = 0
    model_ontology_overlap_count: int = 0
    effective_model_weight: float = 1.0
    effective_long_term_ontology_weight: float = 0.0
    model_ontology_agreement: float = 0.0


@dataclass(frozen=True, slots=True)
class LongTermOntologyRetrievalDiagnostics:
    ontology_build_id: int
    profile_feature_count: int
    excluded_movie_count: int
    candidate_count: int
    elapsed_seconds: float
    query_count: int


@dataclass(frozen=True, slots=True)
class LongTermOntologyRetrievalResult:
    candidates: tuple[LongTermOntologyCandidate, ...]
    diagnostics: LongTermOntologyRetrievalDiagnostics


@dataclass(frozen=True, slots=True)
class CandidateMergeResult:
    candidates: tuple[MergedCandidate, ...]
    diagnostics: CandidateMergeDiagnostics


@dataclass(frozen=True, slots=True)
class ColdStartRetrievalDiagnostics:
    ontology_build_id: int
    strategy: ColdStartStrategy
    profile_feature_count: int
    excluded_movie_count: int
    candidate_count: int
    ontology_cold_item_count: int
    query_count: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ColdStartRetrievalResult:
    candidates: tuple[ColdStartCandidate, ...]
    diagnostics: ColdStartRetrievalDiagnostics


@dataclass(frozen=True, slots=True)
class ColdStartMergeDiagnostics:
    feature_only_model_count: int
    rule_candidate_count: int
    raw_union_count: int
    selected_count: int
    feature_only_model_weight: float


@dataclass(frozen=True, slots=True)
class ColdStartMergeResult:
    candidates: tuple[MergedCandidate, ...]
    diagnostics: ColdStartMergeDiagnostics


@dataclass(frozen=True, slots=True)
class ColdStartPipelineResult:
    retrieval: ColdStartRetrievalResult
    merged: ColdStartMergeResult
    ontology: OntologyAnalysisResult
    elapsed_seconds: float
    eligibility: CandidateEligibilityDiagnostics = field(
        default_factory=CandidateEligibilityDiagnostics
    )
    prefilter_rejections: tuple[HardFilterRejection, ...] = ()

    def __post_init__(self) -> None:
        merged_ids = tuple(item.movie_id for item in self.merged.candidates)
        ontology_ids = tuple(item.movie_id for item in self.ontology.candidates)
        if merged_ids != ontology_ids:
            raise ValueError("cold-start pipeline candidate and ontology order must match")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("cold-start pipeline elapsed time must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class OntologyTypeScore:
    feature: FeatureName
    long_positive_score: float = 0.0
    long_negative_score: float = 0.0
    short_positive_score: float = 0.0
    short_negative_score: float = 0.0
    long_positive_match_count: int = 0
    long_negative_match_count: int = 0
    short_positive_match_count: int = 0
    short_negative_match_count: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("long_positive_score", self.long_positive_score),
            ("long_negative_score", self.long_negative_score),
            ("short_positive_score", self.short_positive_score),
            ("short_negative_score", self.short_negative_score),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"ontology {name} must be finite and non-negative")
        for name, value in (
            ("long_positive_match_count", self.long_positive_match_count),
            ("long_negative_match_count", self.long_negative_match_count),
            ("short_positive_match_count", self.short_positive_match_count),
            ("short_negative_match_count", self.short_negative_match_count),
        ):
            if value < 0:
                raise ValueError(f"ontology {name} cannot be negative")


@dataclass(frozen=True, slots=True)
class OttCandidateEvidence:
    streaming_ott_ids: frozenset[int] = field(default_factory=frozenset)
    subscribed_streaming_ott_ids: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.streaming_ott_ids):
            raise ValueError("streaming OTT IDs must be positive")
        if not self.subscribed_streaming_ott_ids <= self.streaming_ott_ids:
            raise ValueError("subscribed streaming OTT IDs must be available")


@dataclass(frozen=True, slots=True)
class CandidateOntologyAnalysis:
    movie_id: int
    type_scores: tuple[OntologyTypeScore, ...]
    long_positive_total: float
    long_negative_total: float
    short_positive_total: float
    short_negative_total: float
    ott: OttCandidateEvidence
    repetition_features: CandidateFeatureSet = field(default_factory=lambda: CandidateFeatureSet())

    def __post_init__(self) -> None:
        if self.movie_id <= 0:
            raise ValueError("ontology analysis movie ID must be positive")
        if len({item.feature for item in self.type_scores}) != len(self.type_scores):
            raise ValueError("ontology analysis feature types must be unique")
        for value in (
            self.long_positive_total,
            self.long_negative_total,
            self.short_positive_total,
            self.short_negative_total,
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError("ontology analysis totals must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CandidateFeatureSet:
    genre: frozenset[str] = field(default_factory=frozenset)
    actor: frozenset[str] = field(default_factory=frozenset)
    director: frozenset[str] = field(default_factory=frozenset)
    theme: frozenset[str] = field(default_factory=frozenset)
    mood: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for values in (self.genre, self.actor, self.director, self.theme, self.mood):
            if any(not value.strip() for value in values):
                raise ValueError("candidate repetition feature IDs cannot be empty")


@dataclass(frozen=True, slots=True)
class OntologyAnalyzerDiagnostics:
    ontology_build_id: int
    candidate_count: int
    matched_candidate_count: int
    aggregate_row_count: int
    repetition_feature_row_count: int
    streaming_ott_row_count: int
    query_count: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class OntologyAnalysisResult:
    candidates: tuple[CandidateOntologyAnalysis, ...]
    diagnostics: OntologyAnalyzerDiagnostics


@dataclass(frozen=True, slots=True)
class ShortTermRetrievalDiagnostics:
    ontology_build_id: int
    profile_feature_count: int
    excluded_movie_count: int
    candidate_count: int
    elapsed_seconds: float
    query_count: int
    cache_status: str = "not_used"
    profile_signature: str | None = None


@dataclass(frozen=True, slots=True)
class ShortTermRetrievalResult:
    candidates: tuple[ShortTermCandidate, ...]
    diagnostics: ShortTermRetrievalDiagnostics


@dataclass(frozen=True, slots=True)
class RetrievalPipelineResult:
    short_term: ShortTermRetrievalResult
    merged: CandidateMergeResult
    ontology: OntologyAnalysisResult
    elapsed_seconds: float
    long_term_ontology: LongTermOntologyRetrievalResult | None = None
    eligibility: CandidateEligibilityDiagnostics = field(
        default_factory=CandidateEligibilityDiagnostics
    )
    prefilter_rejections: tuple[HardFilterRejection, ...] = ()

    def __post_init__(self) -> None:
        merged_ids = tuple(item.movie_id for item in self.merged.candidates)
        ontology_ids = tuple(item.movie_id for item in self.ontology.candidates)
        if merged_ids != ontology_ids:
            raise ValueError("retrieval pipeline candidate and ontology order must match")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("retrieval pipeline elapsed time must be finite and non-negative")


def _validate_candidate(movie_id: int, score: float, rank: int) -> None:
    if movie_id <= 0 or rank <= 0:
        raise ValueError("candidate movie ID and rank must be positive")
    if not math.isfinite(score):
        raise ValueError("candidate score must be finite")
