from __future__ import annotations

import time
from collections.abc import Sequence

from redis import Redis
from sqlalchemy.orm import Session

from app.services.recsys.v4.domain.schemas import UserProfileBundle
from app.services.recsys.v4.policy.policy_schemas import PolicyRequestContext
from app.services.recsys.v4.retrieval.candidate_eligibility import (
    select_eligible_candidates,
)
from app.services.recsys.v4.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
)
from app.services.recsys.v4.retrieval.ontology_analyzer import analyze_candidates
from app.services.recsys.v4.retrieval.retrieval_schemas import (
    CandidateMergeResult,
    LongTermCandidate,
    RetrievalPipelineResult,
)
from app.services.recsys.v4.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
)
from app.services.recsys.v4.serving.model_store import RuntimeHybridArtifact
from app.services.recsys.v4.config import CANDIDATE_POOL_SIZE, CANDIDATE_STORAGE_SIZE
from app.services.recsys.v4.retrieval.candidate_merger import (
    merge_lightfm_and_short_term_candidates,
)


def build_retrieval_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    long_term_candidates: Sequence[LongTermCandidate],
    context: PolicyRequestContext,
    artifact: RuntimeHybridArtifact,
    redis: Redis | None = None,
    collaborative_population_confidence: float = 1.0,
    limit: int = CANDIDATE_POOL_SIZE,
) -> RetrievalPipelineResult:
    """Build known-user V4 candidates without direct long-term ontology retrieval."""
    started = time.monotonic()
    short_term = retrieve_cached_short_term_candidates(
        db,
        redis=redis,
        ontology_build_id=ontology_build_id,
        profile=profile,
        limit=limit,
    )
    collaborative = assess_user_collaborative_confidence(
        positive_pair_count=profile.long_term.positive_pair_count,
        population_confidence=collaborative_population_confidence,
    )
    merged = merge_lightfm_and_short_term_candidates(
        long_term_candidates,
        short_term.candidates,
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
        movie_identity_supported=artifact.movie_identity_supported,
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
        artifact=artifact,
    )
    return RetrievalPipelineResult(
        short_term=short_term,
        merged=selected_merged,
        ontology=ontology,
        elapsed_seconds=round(time.monotonic() - started, 6),
        eligibility=eligibility.diagnostics,
        prefilter_rejections=eligibility.rejections,
    )
