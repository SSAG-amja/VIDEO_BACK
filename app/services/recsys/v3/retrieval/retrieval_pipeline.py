from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy.orm import Session
from redis import Redis

from app.services.recsys.v3.retrieval.candidate_eligibility import select_eligible_candidates
from app.services.recsys.v3.retrieval.candidate_merger import merge_candidates
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
)
from app.services.recsys.v3.config import CANDIDATE_POOL_SIZE, CANDIDATE_STORAGE_SIZE
from app.services.recsys.v3.retrieval.ontology_analyzer import analyze_candidates
from app.services.recsys.v3.retrieval.long_term_ontology_retriever import (
    retrieve_long_term_ontology_candidates,
)
from app.services.recsys.v3.policy.policy_schemas import PolicyRequestContext
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateMergeResult,
    LongTermCandidate,
    RetrievalPipelineResult,
)
from app.services.recsys.v3.domain.schemas import UserProfileBundle
from app.services.recsys.v3.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
)


def build_retrieval_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    long_term_candidates: Sequence[LongTermCandidate],
    context: PolicyRequestContext,
    redis: Redis | None = None,
    collaborative_population_confidence: float = 1.0,
    limit: int = CANDIDATE_POOL_SIZE,
) -> RetrievalPipelineResult:
    started = time.monotonic()
    short_term = retrieve_cached_short_term_candidates(
        db,
        redis=redis,
        ontology_build_id=ontology_build_id,
        profile=profile,
        limit=limit,
    )
    long_term_ontology = retrieve_long_term_ontology_candidates(
        db,
        ontology_build_id=ontology_build_id,
        profile=profile,
        limit=limit,
    )
    collaborative = assess_user_collaborative_confidence(
        positive_pair_count=profile.long_term.positive_pair_count,
        population_confidence=collaborative_population_confidence,
    )
    merged = merge_candidates(
        long_term_candidates,
        short_term.candidates,
        long_term_ontology.candidates,
        drift_confidence=profile.short_term.drift_confidence,
        collaborative_population_confidence=collaborative.population_confidence,
        collaborative_user_evidence_confidence=collaborative.user_evidence_confidence,
        collaborative_effective_confidence=collaborative.effective_confidence,
        limit=CANDIDATE_STORAGE_SIZE,
    )
    eligibility = select_eligible_candidates(
        db,
        candidates=merged.candidates,
        profile=profile,
        context=context,
        limit=limit,
    )
    selected_merged = CandidateMergeResult(
        candidates=eligibility.candidates,
        diagnostics=merged.diagnostics,
    )
    ontology = analyze_candidates(
        db,
        ontology_build_id=ontology_build_id,
        candidate_movie_ids=[item.movie_id for item in eligibility.candidates],
        profile=profile,
    )
    return RetrievalPipelineResult(
        short_term=short_term,
        merged=selected_merged,
        ontology=ontology,
        elapsed_seconds=round(time.monotonic() - started, 6),
        long_term_ontology=long_term_ontology,
        eligibility=eligibility.diagnostics,
        prefilter_rejections=eligibility.rejections,
    )
