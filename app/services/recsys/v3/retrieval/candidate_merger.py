from __future__ import annotations

from collections.abc import Sequence

from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    CANDIDATE_STORAGE_SIZE,
    LONG_TERM_MODEL_MIN_SELECTION_WEIGHT,
    LONG_TERM_MODEL_SELECTION_WEIGHT,
    LONG_TERM_ONTOLOGY_MIN_RATIO,
    LONG_TERM_ONTOLOGY_SELECTION_WEIGHT,
    LONG_TERM_SEMANTIC_AGREEMENT_TOP_K,
    SHORT_TERM_CONTEXT_FLOOR_MAX_RATIO,
    SHORT_TERM_CONTEXT_FLOOR_MIN_DRIFT,
    SHORT_TERM_DRIFT_MAX_WEIGHT,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateMergeDiagnostics,
    CandidateMergeResult,
    CandidateSource,
    LongTermCandidate,
    LongTermOntologyCandidate,
    MergedCandidate,
    ShortTermCandidate,
)
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize


def merge_candidates(
    long_term_candidates: Sequence[LongTermCandidate],
    short_term_candidates: Sequence[ShortTermCandidate],
    long_term_ontology_candidates: Sequence[LongTermOntologyCandidate] = (),
    *,
    drift_confidence: float,
    limit: int = CANDIDATE_POOL_SIZE,
) -> CandidateMergeResult:
    if limit <= 0 or limit > CANDIDATE_STORAGE_SIZE:
        raise ValueError(f"candidate merge limit must be between 1 and {CANDIDATE_STORAGE_SIZE}")
    if not 0.0 <= drift_confidence <= 1.0:
        raise ValueError("drift confidence must be between 0 and 1")
    _validate_unique_candidates(long_term_candidates, "long-term")
    _validate_unique_candidates(short_term_candidates, "short-term")
    _validate_unique_candidates(long_term_ontology_candidates, "long-term ontology")

    model_by_movie = {item.movie_id: item for item in long_term_candidates}
    short_by_movie = {item.movie_id: item for item in short_term_candidates}
    ontology_by_movie = {
        item.movie_id: item for item in long_term_ontology_candidates
    }
    normalized_long = percentile_normalize(
        {item.movie_id: item.model_raw_score for item in long_term_candidates}
    )
    normalized_short = percentile_normalize(
        {item.movie_id: item.short_term_raw_score for item in short_term_candidates}
    )
    normalized_ontology = percentile_normalize(
        {
            item.movie_id: item.ontology_raw_score
            for item in long_term_ontology_candidates
        }
    )
    if model_by_movie and ontology_by_movie:
        agreement_denominator = min(
            LONG_TERM_SEMANTIC_AGREEMENT_TOP_K,
            len(long_term_candidates),
        )
        top_model_ids = {
            item.movie_id
            for item in sorted(long_term_candidates, key=lambda item: item.source_rank)[
                :LONG_TERM_SEMANTIC_AGREEMENT_TOP_K
            ]
        }
        top_ontology_ids = {
            item.movie_id
            for item in sorted(
                long_term_ontology_candidates,
                key=lambda item: item.source_rank,
            )[:LONG_TERM_SEMANTIC_AGREEMENT_TOP_K]
        }
        semantic_agreement = (
            len(top_model_ids & top_ontology_ids) / agreement_denominator
            if agreement_denominator
            else 0.0
        )
        model_weight = LONG_TERM_MODEL_MIN_SELECTION_WEIGHT + (
            LONG_TERM_MODEL_SELECTION_WEIGHT
            - LONG_TERM_MODEL_MIN_SELECTION_WEIGHT
        ) * semantic_agreement
        ontology_weight = LONG_TERM_ONTOLOGY_SELECTION_WEIGHT + (
            LONG_TERM_MODEL_SELECTION_WEIGHT - model_weight
        )
    elif model_by_movie:
        semantic_agreement = 0.0
        model_weight = 1.0
        ontology_weight = 0.0
    else:
        semantic_agreement = 0.0
        model_weight = 0.0
        ontology_weight = 1.0
    drift_weight = round(drift_confidence * SHORT_TERM_DRIFT_MAX_WEIGHT, 8)
    union_ids = set(model_by_movie) | set(ontology_by_movie) | set(short_by_movie)
    selection_scores = {
        movie_id: round(
            (1.0 - drift_weight)
            * (
                model_weight * normalized_long.get(movie_id, 0.0)
                + ontology_weight * normalized_ontology.get(movie_id, 0.0)
            )
            + (drift_weight * normalized_short.get(movie_id, 0.0)),
            8,
        )
        for movie_id in union_ids
    }
    global_order = sorted(
        union_ids,
        key=lambda movie_id: (
            -selection_scores[movie_id],
            movie_id not in model_by_movie,
            model_by_movie[movie_id].source_rank if movie_id in model_by_movie else 10**9,
            (
                ontology_by_movie[movie_id].source_rank
                if movie_id in ontology_by_movie
                else 10**9
            ),
            short_by_movie[movie_id].source_rank if movie_id in short_by_movie else 10**9,
            movie_id,
        ),
    )

    protected_ids: list[int] = []
    base_pool_size = min(CANDIDATE_POOL_SIZE, limit)
    ontology_floor_count = 0
    if model_by_movie and ontology_by_movie:
        ontology_floor_count = min(
            len(ontology_by_movie),
            max(1, round(base_pool_size * LONG_TERM_ONTOLOGY_MIN_RATIO)),
        )
        protected_ids.extend(
            sorted(
                ontology_by_movie,
                key=lambda movie_id: (
                    -normalized_ontology[movie_id],
                    ontology_by_movie[movie_id].source_rank,
                    movie_id,
                ),
            )[:ontology_floor_count]
        )

    contextual_floor_count = 0
    short_only_ids = (
        set(short_by_movie) - set(model_by_movie) - set(ontology_by_movie)
    )
    if short_only_ids and drift_confidence >= SHORT_TERM_CONTEXT_FLOOR_MIN_DRIFT:
        contextual_floor_count = min(
            len(short_only_ids),
            max(
                1,
                round(
                    base_pool_size
                    * min(drift_weight, SHORT_TERM_CONTEXT_FLOOR_MAX_RATIO)
                ),
            ),
        )
        floor_ids = sorted(
            short_only_ids,
            key=lambda movie_id: (
                -normalized_short[movie_id],
                short_by_movie[movie_id].source_rank,
                movie_id,
            ),
        )[:contextual_floor_count]
        protected_ids.extend(
            movie_id for movie_id in floor_ids if movie_id not in protected_ids
        )

    protected_set = set(protected_ids)
    selected_ids = (
        protected_ids
        + [movie_id for movie_id in global_order if movie_id not in protected_set]
    )[:limit]

    merged: list[MergedCandidate] = []
    for rank, movie_id in enumerate(selected_ids, start=1):
        model = model_by_movie.get(movie_id)
        long_term_ontology = ontology_by_movie.get(movie_id)
        short_term = short_by_movie.get(movie_id)
        sources = tuple(
            source
            for source, present in (
                (CandidateSource.MODEL, model is not None),
                (CandidateSource.LONG_TERM_ONTOLOGY, long_term_ontology is not None),
                (CandidateSource.SHORT_TERM_CONTEXT, short_term is not None),
            )
            if present
        )
        merged.append(
            MergedCandidate(
                movie_id=movie_id,
                sources=sources,
                selection_rank=rank,
                candidate_selection_score=selection_scores[movie_id],
                model_raw_score=model.model_raw_score if model else None,
                normalized_long_term_score=normalized_long.get(movie_id, 0.0),
                model_source_rank=model.source_rank if model else None,
                long_term_ontology_raw_score=(
                    long_term_ontology.ontology_raw_score
                    if long_term_ontology
                    else None
                ),
                normalized_long_term_ontology_score=normalized_ontology.get(
                    movie_id, 0.0
                ),
                long_term_ontology_source_rank=(
                    long_term_ontology.source_rank if long_term_ontology else None
                ),
                short_term_raw_score=(
                    short_term.short_term_raw_score if short_term else None
                ),
                normalized_short_term_score=normalized_short.get(movie_id, 0.0),
                short_term_source_rank=short_term.source_rank if short_term else None,
            )
        )
    selected_model_only_count = sum(
        item.movie_id in model_by_movie
        and item.movie_id not in ontology_by_movie
        and item.movie_id not in short_by_movie
        for item in merged
    )
    selected_short_only_count = sum(
        item.movie_id in short_by_movie
        and item.movie_id not in model_by_movie
        and item.movie_id not in ontology_by_movie
        for item in merged
    )
    selected_overlap_count = sum(
        item.movie_id in model_by_movie and item.movie_id in short_by_movie
        for item in merged
    )
    return CandidateMergeResult(
        candidates=tuple(merged),
        diagnostics=CandidateMergeDiagnostics(
            long_term_source_count=len(long_term_candidates),
            short_term_source_count=len(short_term_candidates),
            raw_union_count=len(union_ids),
            selected_count=len(merged),
            drift_confidence=drift_confidence,
            drift_weight=drift_weight,
            contextual_floor_count=contextual_floor_count,
            selected_model_only_count=selected_model_only_count,
            selected_short_only_count=selected_short_only_count,
            selected_overlap_count=selected_overlap_count,
            long_term_ontology_source_count=len(long_term_ontology_candidates),
            long_term_ontology_floor_count=ontology_floor_count,
            selected_long_term_ontology_count=sum(
                item.movie_id in ontology_by_movie for item in merged
            ),
            selected_long_term_ontology_only_count=sum(
                item.movie_id in ontology_by_movie
                and item.movie_id not in model_by_movie
                and item.movie_id not in short_by_movie
                for item in merged
            ),
            model_ontology_overlap_count=len(
                set(model_by_movie) & set(ontology_by_movie)
            ),
            effective_model_weight=model_weight,
            effective_long_term_ontology_weight=ontology_weight,
            model_ontology_agreement=semantic_agreement,
        ),
    )


def _validate_unique_candidates(candidates: Sequence, source: str) -> None:
    movie_ids = [item.movie_id for item in candidates]
    if len(movie_ids) != len(set(movie_ids)):
        raise ValueError(f"{source} candidates contain duplicate movie IDs")
