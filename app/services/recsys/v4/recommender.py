from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.schemas.recsys import RecommendationMode, RecommendationResponse
from app.services.recsys.v1.interaction_cache import get_blacklisted_movie_ids
from app.services.recsys.v4.cold_start.cold_start_pipeline import (
    run_cold_start_pipeline,
)
from app.services.recsys.v4.domain.schemas import OttFilterMode, UserProfileBundle
from app.services.recsys.v4.policy.policy_engine import (
    evaluate_candidate_set,
    evaluate_policy_candidates,
)
from app.services.recsys.v4.policy.policy_schemas import (
    PolicyRequestContext,
    RankedPolicyCandidate,
)
from app.services.recsys.v4.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v4.retrieval.lightfm_retriever import (
    onboarding_features_changed,
    retrieve_initially_eligible_lightfm_candidates,
)
from app.services.recsys.v4.retrieval.retrieval_schemas import CandidateSource
from app.services.recsys.v4.serving.model_store import MovieIdIndex
from app.services.recsys.v4.config import CANDIDATE_POOL_SIZE
from app.services.recsys.v4.diagnostics import persist_request_diagnostics
from app.services.recsys.v4.retrieval.retrieval_pipeline import (
    build_retrieval_candidates,
)
from app.services.recsys.v4.serving.artifact_contract import (
    get_active_v4_serving_bundle,
)
from app.services.recsys.v4.serving.candidate_store import (
    load_published_candidates,
    persist_feature_only_candidates,
)


def get_recommendations(
    db: Session,
    *,
    user_id: int,
    mode: RecommendationMode,
    limit: int,
    offset: int = 0,
    shuffle_seed: str | None = None,
) -> RecommendationResponse:
    if user_id <= 0 or limit <= 0 or offset < 0:
        raise ValueError("V4 recommendation user, limit, and offset are invalid")
    redis = get_redis()
    bundle = get_active_v4_serving_bundle()
    model_user_known = bundle.model.user_index(user_id) is not None
    profile = build_user_runtime_profile(
        db,
        user_id=user_id,
        ontology_build_id=bundle.ontology_build_id,
        as_of=datetime.now(timezone.utc),
        model_user_known=model_user_known,
        ott_mode=OttFilterMode(mode.value),
    ).bundle
    published_candidates, published_kind = load_published_candidates(
        db,
        bundle=bundle,
        profile=profile,
    )
    if model_user_known and onboarding_features_changed(bundle.model, profile):
        if published_kind != "feature_only":
            published_candidates = ()
        published_kind = "feature_only"
    excluded = (
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
    )

    request_path: dict[str, object]
    if published_kind == "feature_only" or not model_user_known:
        context = _policy_context(
            redis,
            user_id,
            profile,
            genre_only_cold_start=(
                bool(profile.onboarding.genre_ids)
                and not profile.onboarding.favorite_movie_ids
            ),
        )
        feature_only = (
            published_candidates
            or retrieve_initially_eligible_lightfm_candidates(
                db,
                bundle.model,
                profile=profile,
                excluded_movie_ids=excluded,
                as_of=context.as_of,
                force_feature_only=True,
            )
        )
        if feature_only and not published_candidates:
            persist_feature_only_candidates(
                db,
                bundle=bundle,
                profile=profile,
                candidates=feature_only,
                suppress_errors=True,
            )
        cold_start = run_cold_start_pipeline(
            db,
            ontology_build_id=bundle.ontology_build_id,
            profile=profile,
            context=context,
            artifact=bundle.model,
            feature_only_model_candidates=feature_only,
            model_known_movie_ids=MovieIdIndex(bundle.model.movie_ids),
        )
        policy = evaluate_candidate_set(
            db,
            candidates=cold_start.merged.candidates,
            analyses=cold_start.ontology.candidates,
            profile=profile,
            context=context,
            prior_rejections=cold_start.prefilter_rejections,
            input_candidate_count=cold_start.eligibility.input_candidate_count,
        )
        request_path = {
            "candidate_path": "v4_cold_start",
            "published_candidate_kind": published_kind,
            "direct_ontology_role": "cold_start",
            "candidate_eligibility": asdict(cold_start.eligibility),
        }
    else:
        context = _policy_context(redis, user_id, profile)
        long_term = (
            published_candidates
            or retrieve_initially_eligible_lightfm_candidates(
                db,
                bundle.model,
                profile=profile,
                excluded_movie_ids=excluded,
                as_of=context.as_of,
            )
        )
        retrieval = build_retrieval_candidates(
            db,
            ontology_build_id=bundle.ontology_build_id,
            profile=profile,
            long_term_candidates=long_term,
            context=context,
            artifact=bundle.model,
            redis=redis,
            collaborative_population_confidence=(
                bundle.model.collaborative_population_confidence
            ),
        )
        policy = evaluate_policy_candidates(
            db,
            retrieval=retrieval,
            profile=profile,
            context=context,
        )
        request_path = {
            "candidate_path": "v4_known_user_hybrid_lightfm",
            "long_term_candidate_owner": "hybrid_lightfm",
            "direct_long_term_ontology_enabled": False,
            "published_candidate_kind": published_kind,
            "short_term_cache_status": retrieval.short_term.diagnostics.cache_status,
            "short_term_profile_signature": (
                retrieval.short_term.diagnostics.profile_signature
            ),
            "short_term_candidate_count": len(retrieval.short_term.candidates),
            "candidate_merge": asdict(retrieval.merged.diagnostics),
            "eligible_source_counts": _candidate_source_counts(
                retrieval.merged.candidates
            ),
            "candidate_eligibility": asdict(retrieval.eligibility),
        }

    page = policy.candidates[offset : offset + limit]
    has_more = len(policy.candidates) > offset + limit
    persist_request_diagnostics(
        db,
        bundle=bundle,
        user_id=user_id,
        shuffle_seed=shuffle_seed,
        policy=policy,
        page=page,
        request_path=request_path,
    )
    return RecommendationResponse(
        user_id=user_id,
        mode=mode,
        movie_ids=[item.movie_id for item in page],
        limit=limit,
        offset=offset,
        count=len(page),
        has_more=has_more,
        source=_page_source(page),
    )


def _policy_context(
    redis,
    user_id: int,
    profile: UserProfileBundle,
    *,
    genre_only_cold_start: bool = False,
) -> PolicyRequestContext:
    return PolicyRequestContext(
        as_of=profile.serving_context.availability_as_of,
        limit=CANDIDATE_POOL_SIZE,
        blacklisted_movie_ids=frozenset(
            get_blacklisted_movie_ids(redis, user_id)
        ),
        genre_only_cold_start=genre_only_cold_start,
    )


def _page_source(candidates: tuple[RankedPolicyCandidate, ...]) -> str:
    if not candidates:
        return "empty"
    sources = {source for item in candidates for source in item.candidate.sources}
    if sources <= {CandidateSource.MODEL}:
        return "v4_hybrid_lightfm"
    if sources <= {
        CandidateSource.FEATURE_ONLY_MODEL,
        CandidateSource.COLD_START,
        CandidateSource.ONTOLOGY_COLD_ITEM,
    }:
        return "v4_cold_start"
    return "v4_mixed"


def _candidate_source_counts(candidates) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        source = "+".join(item.value for item in candidate.sources)
        counts[source] = counts.get(source, 0) + 1
    return counts
