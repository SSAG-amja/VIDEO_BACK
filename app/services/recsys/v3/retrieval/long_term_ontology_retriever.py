from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.services.recsys.v3.config import (
    INITIAL_CANDIDATE_SCAN_SIZE,
    LONG_TERM_ONTOLOGY_RETRIEVAL_LIMIT,
)
from app.services.recsys.v3.profiles.profile_builder import validate_profile_build
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    LongTermOntologyCandidate,
    LongTermOntologyRetrievalDiagnostics,
    LongTermOntologyRetrievalResult,
)
from app.services.recsys.v3.retrieval.ontology_feature_retriever import (
    retrieve_budgeted_ontology_rows,
    supports_budgeted_ontology_retrieval,
)
from app.services.recsys.v3.retrieval.initial_candidate_filter import (
    select_initial_candidates,
)
from app.services.recsys.v3.retrieval.short_term_retriever import (
    build_short_term_feature_rows,
    load_short_term_candidate_rows,
)
from app.services.recsys.v3.domain.schemas import UserProfileBundle
from app.services.recsys.v3.serving.model_store import RuntimeHybridArtifact


def retrieve_long_term_ontology_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    artifact: RuntimeHybridArtifact | None = None,
    limit: int = LONG_TERM_ONTOLOGY_RETRIEVAL_LIMIT,
    enforce_mature_cold_item_filter: bool = True,
) -> LongTermOntologyRetrievalResult:
    if limit <= 0 or limit > LONG_TERM_ONTOLOGY_RETRIEVAL_LIMIT:
        raise ValueError(
            "long-term ontology retrieval limit must be between "
            f"1 and {LONG_TERM_ONTOLOGY_RETRIEVAL_LIMIT}"
        )
    started = time.monotonic()
    validate_profile_build(db, ontology_build_id)
    feature_rows = build_short_term_feature_rows(profile.long_term.positive_features)
    excluded_movie_ids = frozenset(
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
    )
    use_budgeted_features = (
        artifact is not None
        and artifact.ontology_build_id == ontology_build_id
        and supports_budgeted_ontology_retrieval(artifact)
    )
    if use_budgeted_features:
        rows = retrieve_budgeted_ontology_rows(
            artifact,
            features=profile.long_term.positive_features,
            excluded_movie_ids=excluded_movie_ids,
            limit=INITIAL_CANDIDATE_SCAN_SIZE,
        )
    else:
        rows = load_short_term_candidate_rows(
            db,
            ontology_build_id=ontology_build_id,
            feature_rows=feature_rows,
            excluded_movie_ids=excluded_movie_ids,
            limit=INITIAL_CANDIDATE_SCAN_SIZE,
        )
    raw_candidates = tuple(
        LongTermOntologyCandidate(
            movie_id=int(movie_id),
            ontology_raw_score=float(raw_score),
            source_rank=rank,
        )
        for rank, (movie_id, raw_score) in enumerate(rows, start=1)
    )
    initial_selection = select_initial_candidates(
        db,
        candidates=raw_candidates,
        as_of=profile.serving_context.availability_as_of,
        movie_identity_supported=(
            getattr(artifact, "movie_identity_supported", None)
            if artifact is not None and enforce_mature_cold_item_filter
            else None
        ),
        limit=limit,
    )
    candidates = tuple(initial_selection.candidates)
    return LongTermOntologyRetrievalResult(
        candidates=candidates,
        diagnostics=LongTermOntologyRetrievalDiagnostics(
            ontology_build_id=ontology_build_id,
            profile_feature_count=len(feature_rows),
            excluded_movie_count=len(excluded_movie_ids),
            candidate_count=len(candidates),
            elapsed_seconds=round(time.monotonic() - started, 6),
            query_count=(
                1
                + int(bool(feature_rows) and not use_budgeted_features)
                + int(bool(rows))
            ),
            initial_inspected_candidate_count=(
                initial_selection.inspected_candidate_count
            ),
            initial_rejected_candidate_count=len(initial_selection.rejections),
            initial_rejection_counts=initial_selection.rejection_counts,
        ),
    )
