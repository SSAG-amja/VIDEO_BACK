from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.crud.recsys.ontology import get_active_build
from app.crud.recsys.ontology_recommendations import add_recommendation_snapshots
from app.crud.recsys.movies import load_streaming_movie_ids
from app.crud.recsys.recommendation_runs import create_run, mark_run_finished
from app.models.ontology_recommendations import OntologyRecommendation
from app.schemas.recsys import RecommendationMode, RecommendationResponse
from app.services.recsys.v1.interaction_cache import get_blacklisted_movie_ids
from app.services.recsys.v2.candidate_generator import generate_candidates
from app.services.recsys.v2.config import (
    DEFAULT_CANDIDATE_SLICE_SIZE,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SCORE_CONFIG,
    ENGINE_NAME,
    ENGINE_VERSION,
    MAX_PAGE_SIZE,
)
from app.services.recsys.v2.dynamic_reranker import rerank_for_session
from app.services.recsys.v2.post_processor import apply_safety_filters
from app.services.recsys.v2.profile_builder import build_user_profile
from app.services.recsys.v2.ranker import rank_candidates
from app.services.recsys.v2.schemas import CandidateScore, RecommendationRequestContext
from app.services.recsys.v2.scorer import score_candidates
from app.services.recsys.v2.session_state import load_session_profile, new_feed_session_key, new_request_id


def build_request_context(
    *,
    user_id: int,
    request_id: str | None = None,
    feed_session_key: str | None = None,
    refresh_count: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    subscribed_only: bool = False,
) -> RecommendationRequestContext:
    return RecommendationRequestContext(
        user_id=user_id,
        request_id=request_id or new_request_id(),
        feed_session_key=feed_session_key or new_feed_session_key(),
        refresh_count=refresh_count,
        page_size=min(max(page_size, 1), MAX_PAGE_SIZE),
        offset=max(offset, 0),
        subscribed_only=subscribed_only,
    )


def recommend(db: Session, context: RecommendationRequestContext) -> list[CandidateScore]:
    redis = get_redis()
    profile = build_user_profile(db, context.user_id)
    session_profile = load_session_profile(redis, context.feed_session_key)
    blacklisted_movie_ids = get_blacklisted_movie_ids(redis, context.user_id)
    candidates = generate_candidates(
        db,
        profile=profile,
        session_profile=session_profile,
        limit=max(DEFAULT_CANDIDATE_SLICE_SIZE, context.offset + context.page_size + 1),
        subscribed_only=context.subscribed_only,
    )
    scored = score_candidates(candidates, profile=profile, session_profile=session_profile)
    ranked = rank_candidates(scored)
    reranked = rerank_for_session(ranked, session_profile=session_profile)
    filtered = apply_safety_filters(reranked, profile=profile, extra_excluded_movie_ids=blacklisted_movie_ids)
    if context.subscribed_only:
        filtered = apply_subscribed_only_filter(db, filtered, subscribed_ott_ids=profile.subscribed_ott_ids)
    candidate_slice = filtered[:DEFAULT_CANDIDATE_SLICE_SIZE]
    response_slice = filtered[context.offset : context.offset + context.page_size]
    persist_recommendation_result(
        db,
        context=context,
        candidate_slice=candidate_slice,
        response_slice=response_slice,
    )
    return response_slice


def get_recommendations(
    db: Session,
    *,
    user_id: int,
    mode: RecommendationMode,
    limit: int,
    offset: int = 0,
) -> RecommendationResponse:
    context = build_request_context(
        user_id=user_id,
        page_size=limit,
        offset=offset,
        subscribed_only=mode == RecommendationMode.SUBSCRIBED_ONLY,
    )
    candidates = recommend(db, context)
    has_more = len(candidates) == context.page_size
    return RecommendationResponse(
        user_id=user_id,
        mode=mode,
        movie_ids=[candidate.movie_id for candidate in candidates],
        limit=context.page_size,
        offset=context.offset,
        count=len(candidates),
        has_more=has_more,
        source=page_source(candidates),
    )


def persist_recommendation_result(
    db: Session,
    *,
    context: RecommendationRequestContext,
    candidate_slice: list[CandidateScore],
    response_slice: list[CandidateScore],
) -> None:
    active_build = get_active_build(db)
    run = create_run(
        db,
        run_id=context.request_id,
        engine=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        run_type="request",
        config_snapshot=DEFAULT_SCORE_CONFIG,
        ontology_build_id=active_build.id if active_build else None,
    )
    rows = build_snapshot_rows(
        run_id=run.run_id,
        context=context,
        candidates=candidate_slice,
        candidate_stage="candidate_slice",
        ontology_build_id=active_build.id if active_build else None,
    )
    rows.extend(
        build_snapshot_rows(
            run_id=run.run_id,
            context=context,
            candidates=response_slice,
            candidate_stage="final_response",
            ontology_build_id=active_build.id if active_build else None,
        )
    )
    add_recommendation_snapshots(db, rows)
    source_counts: dict[str, int] = {}
    for candidate in candidate_slice:
        source_counts[candidate.source] = source_counts.get(candidate.source, 0) + 1
    fallback_count = source_counts.get("fallback", 0)
    fallback_ratio = fallback_count / len(candidate_slice) if candidate_slice else 1.0
    mark_run_finished(
        db,
        run,
        status="success",
        processed_user_count=1,
        generated_candidate_count=len(candidate_slice),
        source_counts=source_counts,
        fallback_ratio=fallback_ratio,
    )
    db.commit()


def build_snapshot_rows(
    *,
    run_id: str,
    context: RecommendationRequestContext,
    candidates: list[CandidateScore],
    candidate_stage: str,
    ontology_build_id: int | None,
) -> list[OntologyRecommendation]:
    return [
        OntologyRecommendation(
            run_id=run_id,
            request_id=context.request_id,
            feed_session_key=context.feed_session_key,
            refresh_count=context.refresh_count,
            user_id=context.user_id,
            movie_id=candidate.movie_id,
            rank=index,
            score=candidate.score,
            source=candidate.source,
            source_scores=candidate.source_scores,
            explanation_tags=candidate.explanation_tags,
            ontology_build_id=ontology_build_id,
            engine_version=ENGINE_VERSION,
            candidate_stage=candidate_stage,
        )
        for index, candidate in enumerate(candidates, start=1)
    ]


def page_source(candidates: list[CandidateScore]) -> str:
    if not candidates:
        return "empty"
    sources = {candidate.source for candidate in candidates}
    if len(sources) == 1:
        return next(iter(sources))
    return "mixed"


def apply_subscribed_only_filter(
    db: Session,
    candidates: list[CandidateScore],
    *,
    subscribed_ott_ids: set[int],
) -> list[CandidateScore]:
    if not candidates or not subscribed_ott_ids:
        return []
    movie_ids = [candidate.movie_id for candidate in candidates]
    streaming_movie_ids = load_streaming_movie_ids(db, list(subscribed_ott_ids), movie_ids)
    return [candidate for candidate in candidates if candidate.movie_id in streaming_movie_ids]
