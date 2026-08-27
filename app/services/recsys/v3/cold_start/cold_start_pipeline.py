from __future__ import annotations

import time
from collections.abc import Container, Sequence

from sqlalchemy.orm import Session

from app.services.recsys.v3.retrieval.candidate_eligibility import select_eligible_candidates
from app.services.recsys.v3.cold_start.cold_start_merger import merge_cold_start_candidates
from app.services.recsys.v3.cold_start.cold_start_retriever import retrieve_cold_start_candidates
from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    CANDIDATE_STORAGE_SIZE,
    COLD_START_FEATURE_ONLY_MODEL_WEIGHT,
    COLD_START_GENRE_ONLY_MODEL_WEIGHT,
)
from app.services.recsys.v3.retrieval.ontology_analyzer import analyze_candidates
from app.services.recsys.v3.policy.policy_schemas import PolicyRequestContext
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    ColdStartMergeResult,
    ColdStartPipelineResult,
    LongTermCandidate,
)
from app.services.recsys.v3.domain.schemas import UserProfileBundle


def run_cold_start_pipeline(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    context: PolicyRequestContext,
    feature_only_model_candidates: Sequence[LongTermCandidate] = (),
    model_known_movie_ids: Container[int] = frozenset(),
    limit: int = CANDIDATE_POOL_SIZE,
) -> ColdStartPipelineResult:
    started = time.monotonic()
    favorite_movie_ids = profile.onboarding.favorite_movie_ids
    filtered_model_candidates = tuple(
        candidate
        for candidate in feature_only_model_candidates
        if candidate.movie_id not in favorite_movie_ids
    )
    retrieval = retrieve_cold_start_candidates(
        db,
        ontology_build_id=ontology_build_id,
        profile=profile,
        model_known_movie_ids=model_known_movie_ids,
        limit=limit,
    )
    merged = merge_cold_start_candidates(
        filtered_model_candidates,
        retrieval.candidates,
        limit=CANDIDATE_STORAGE_SIZE,
        feature_only_model_weight=_feature_only_model_weight(profile),
    )
    eligibility = select_eligible_candidates(
        db,
        candidates=merged.candidates,
        profile=profile,
        context=context,
        limit=limit,
    )
    selected_merged = ColdStartMergeResult(
        candidates=eligibility.candidates,
        diagnostics=merged.diagnostics,
    )
    ontology = analyze_candidates(
        db,
        ontology_build_id=ontology_build_id,
        candidate_movie_ids=[item.movie_id for item in eligibility.candidates],
        profile=profile,
        include_onboarding=True,
    )
    return ColdStartPipelineResult(
        retrieval=retrieval,
        merged=selected_merged,
        ontology=ontology,
        elapsed_seconds=round(time.monotonic() - started, 6),
        eligibility=eligibility.diagnostics,
        prefilter_rejections=eligibility.rejections,
    )


def _feature_only_model_weight(profile: UserProfileBundle) -> float:
    if profile.onboarding.favorite_movie_ids:
        return COLD_START_FEATURE_ONLY_MODEL_WEIGHT
    return COLD_START_GENRE_ONLY_MODEL_WEIGHT
