from __future__ import annotations

from collections.abc import Sequence

from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    CANDIDATE_STORAGE_SIZE,
    COLD_START_FEATURE_ONLY_MODEL_WEIGHT,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateSource,
    ColdStartCandidate,
    ColdStartMergeDiagnostics,
    ColdStartMergeResult,
    LongTermCandidate,
    MergedCandidate,
)
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize


def merge_cold_start_candidates(
    feature_only_model_candidates: Sequence[LongTermCandidate],
    rule_candidates: Sequence[ColdStartCandidate],
    *,
    limit: int = CANDIDATE_POOL_SIZE,
    feature_only_model_weight: float | None = None,
) -> ColdStartMergeResult:
    if limit <= 0 or limit > CANDIDATE_STORAGE_SIZE:
        raise ValueError(f"cold-start merge limit must be between 1 and {CANDIDATE_STORAGE_SIZE}")
    _validate_unique(feature_only_model_candidates, "feature-only model")
    _validate_unique(rule_candidates, "cold-start rule")
    model_by_movie = {item.movie_id: item for item in feature_only_model_candidates}
    rule_by_movie = {item.movie_id: item for item in rule_candidates}
    normalized_model = percentile_normalize(
        {item.movie_id: item.model_raw_score for item in feature_only_model_candidates}
    )
    normalized_rule = percentile_normalize(
        {
            item.movie_id: (
                item.rule_selection_score
                if item.rule_selection_score is not None
                else item.raw_score
            )
            for item in rule_candidates
        }
    )
    configured_model_weight = (
        COLD_START_FEATURE_ONLY_MODEL_WEIGHT
        if feature_only_model_weight is None
        else feature_only_model_weight
    )
    if not 0.0 <= configured_model_weight <= 1.0:
        raise ValueError("cold-start feature-only model weight must be between zero and one")
    if model_by_movie and rule_by_movie:
        model_weight = configured_model_weight
    elif model_by_movie:
        model_weight = 1.0
    else:
        model_weight = 0.0
    union_ids = set(model_by_movie) | set(rule_by_movie)
    scores = {
        movie_id: (
            model_weight * normalized_model.get(movie_id, 0.0)
            + (1.0 - model_weight) * normalized_rule.get(movie_id, 0.0)
        )
        for movie_id in union_ids
    }
    selected_ids = sorted(
        union_ids,
        key=lambda movie_id: (
            -scores[movie_id],
            model_by_movie[movie_id].source_rank if movie_id in model_by_movie else 10**9,
            rule_by_movie[movie_id].source_rank if movie_id in rule_by_movie else 10**9,
            movie_id,
        ),
    )[:limit]
    merged: list[MergedCandidate] = []
    for rank, movie_id in enumerate(selected_ids, start=1):
        model = model_by_movie.get(movie_id)
        rule = rule_by_movie.get(movie_id)
        sources = tuple(
            source
            for source in (
                CandidateSource.FEATURE_ONLY_MODEL if model else None,
                rule.source if rule else None,
            )
            if source is not None
        )
        merged.append(
            MergedCandidate(
                movie_id=movie_id,
                sources=sources,
                selection_rank=rank,
                candidate_selection_score=round(scores[movie_id], 8),
                model_raw_score=model.model_raw_score if model else None,
                normalized_long_term_score=normalized_model.get(movie_id, 0.0),
                model_source_rank=model.source_rank if model else None,
                cold_start_raw_score=rule.raw_score if rule else None,
                normalized_cold_start_score=normalized_rule.get(movie_id, 0.0),
                cold_start_source_rank=rule.source_rank if rule else None,
                cold_start_overview_support_score=(
                    rule.overview_support_score if rule else 0.0
                ),
                cold_start_rule_selection_score=(
                    rule.rule_selection_score if rule else None
                ),
                cold_start_quality_score=rule.quality_score if rule else 0.0,
                cold_start_genre_relevance_score=(
                    rule.genre_relevance_score if rule else 0.0
                ),
                cold_start_trusted_quality=(
                    rule.trusted_quality if rule else False
                ),
            )
        )
    return ColdStartMergeResult(
        candidates=tuple(merged),
        diagnostics=ColdStartMergeDiagnostics(
            feature_only_model_count=len(feature_only_model_candidates),
            rule_candidate_count=len(rule_candidates),
            raw_union_count=len(union_ids),
            selected_count=len(merged),
            feature_only_model_weight=model_weight,
        ),
    )


def _validate_unique(candidates: Sequence, source: str) -> None:
    movie_ids = [item.movie_id for item in candidates]
    if len(movie_ids) != len(set(movie_ids)):
        raise ValueError(f"{source} candidates contain duplicate movie IDs")
