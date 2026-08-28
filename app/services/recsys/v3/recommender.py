from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.crud.recsys.ontology_recommendations import add_recommendation_snapshots
from app.crud.recsys.recommendation_runs import create_run, mark_run_finished
from app.crud.recsys.recommendations import (
    load_v3_candidate_rows,
    replace_user_recommendation_rows,
)
from app.models.ontology_recommendations import OntologyRecommendation
from app.models.recommendations import Recommendation
from app.schemas.recsys import RecommendationMode, RecommendationResponse
from app.services.recsys.v1.interaction_cache import get_blacklisted_movie_ids
from app.services.recsys.v3.cold_start.cold_start_pipeline import run_cold_start_pipeline
from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    CANDIDATE_STORAGE_SIZE,
    ENGINE_NAME,
    ENGINE_VERSION,
)
from app.services.recsys.v3.retrieval.lightfm_retriever import (
    onboarding_features_changed,
    onboarding_profile_signature,
    retrieve_lightfm_candidates,
)
from app.services.recsys.v3.serving.model_store import MovieIdIndex
from app.services.recsys.v3.policy.policy_config import policy_config_snapshot
from app.services.recsys.v3.policy.policy_engine import evaluate_candidate_set, evaluate_policy_candidates
from app.services.recsys.v3.policy.policy_schemas import (
    PolicyEvaluationResult,
    PolicyRequestContext,
    RankedPolicyCandidate,
)
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.retrieval.retrieval_pipeline import build_retrieval_candidates
from app.services.recsys.v3.retrieval.retrieval_schemas import CandidateSource, LongTermCandidate
from app.services.recsys.v3.domain.schemas import OttFilterMode, UserProfileBundle
from app.services.recsys.v3.serving.serving_bundle import ServingBundle, get_active_serving_bundle


logger = logging.getLogger(__name__)


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
        raise ValueError("V3 recommendation user, limit, and offset are invalid")
    redis = get_redis()
    bundle = get_active_serving_bundle()
    model_user_known = bundle.model.user_index(user_id) is not None
    profile = build_user_runtime_profile(
        db,
        user_id=user_id,
        ontology_build_id=bundle.ontology_build_id,
        as_of=datetime.now(timezone.utc),
        model_user_known=model_user_known,
        ott_mode=OttFilterMode(mode.value),
    ).bundle
    published_candidates, published_kind = _load_published_candidates(
        db,
        bundle=bundle,
        profile=profile,
    )
    if model_user_known and onboarding_features_changed(bundle.model, profile):
        if published_kind != "feature_only":
            published_candidates = ()
        published_kind = "feature_only"
    excluded = profile.long_term.excluded_movie_ids | profile.short_term.recent_negative_movie_ids

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
        feature_only = published_candidates or retrieve_lightfm_candidates(
            bundle.model,
            profile=profile,
            excluded_movie_ids=excluded,
            force_feature_only=True,
        )
        if feature_only and not published_candidates:
            _persist_feature_only_candidates(
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
            "candidate_path": "cold_start",
            "published_candidate_kind": published_kind,
            "candidate_eligibility": asdict(cold_start.eligibility),
        }
    else:
        context = _policy_context(redis, user_id, profile)
        long_term = published_candidates or retrieve_lightfm_candidates(
            bundle.model,
            profile=profile,
            excluded_movie_ids=excluded,
        )
        retrieval = build_retrieval_candidates(
            db,
            ontology_build_id=bundle.ontology_build_id,
            profile=profile,
            long_term_candidates=long_term,
            context=context,
            redis=redis,
        )
        policy = evaluate_policy_candidates(
            db,
            retrieval=retrieval,
            profile=profile,
            context=context,
        )
        merged_result = getattr(retrieval, "merged", None)
        long_term_ontology_result = getattr(
            retrieval,
            "long_term_ontology",
            None,
        )
        request_path = {
            "candidate_path": "known_user_hybrid",
            "published_candidate_kind": published_kind,
            "short_term_cache_status": retrieval.short_term.diagnostics.cache_status,
            "short_term_profile_signature": retrieval.short_term.diagnostics.profile_signature,
            "short_term_candidate_count": len(retrieval.short_term.candidates),
            "long_term_ontology_candidate_count": (
                len(long_term_ontology_result.candidates)
                if long_term_ontology_result is not None
                else 0
            ),
            "candidate_merge": (
                asdict(merged_result.diagnostics) if merged_result is not None else {}
            ),
            "eligible_source_counts": (
                _candidate_source_counts(merged_result.candidates)
                if merged_result is not None
                else {}
            ),
            "candidate_eligibility": asdict(retrieval.eligibility),
        }

    page = policy.candidates[offset : offset + limit]
    has_more = len(policy.candidates) > offset + limit
    _persist_request_diagnostics(
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


def refresh_cold_start(db: Session, *, user_id: int) -> None:
    bundle = get_active_serving_bundle()
    profile = build_user_runtime_profile(
        db,
        user_id=user_id,
        ontology_build_id=bundle.ontology_build_id,
        as_of=datetime.now(timezone.utc),
        model_user_known=bundle.model.user_index(user_id) is not None,
        ott_mode=OttFilterMode.ALL,
    ).bundle
    candidates = retrieve_lightfm_candidates(
        bundle.model,
        profile=profile,
        excluded_movie_ids=profile.long_term.excluded_movie_ids,
        force_feature_only=True,
    )
    if not candidates:
        return
    _persist_feature_only_candidates(
        db,
        bundle=bundle,
        profile=profile,
        candidates=candidates,
        suppress_errors=False,
    )


def _persist_feature_only_candidates(
    db: Session,
    *,
    bundle: ServingBundle,
    profile: UserProfileBundle,
    candidates: tuple[LongTermCandidate, ...],
    suppress_errors: bool,
) -> bool:
    signature = onboarding_profile_signature(profile)
    rows = [
        Recommendation(
            user_id=profile.user_id,
            movie_id=item.movie_id,
            score=item.model_raw_score,
            rank=item.source_rank,
            source="lightfm_v3_feature_only",
            source_scores={
                "candidate_kind": "feature_only",
                "model_raw_score": item.model_raw_score,
                "model_source_rank": item.source_rank,
                "model_build_id": bundle.model.model_build_id,
                "serving_bundle_id": bundle.bundle_id,
                "onboarding_profile_signature": signature,
            },
        )
        for item in candidates
    ]
    try:
        replace_user_recommendation_rows(db, profile.user_id, rows)
        db.commit()
        return True
    except Exception:
        db.rollback()
        if not suppress_errors:
            raise
        logger.warning(
            "V3 feature-only candidate persistence failed user_id=%s",
            profile.user_id,
            exc_info=True,
        )
        return False


def _load_published_candidates(
    db: Session,
    *,
    bundle: ServingBundle,
    profile: UserProfileBundle,
) -> tuple[tuple[LongTermCandidate, ...], str | None]:
    rows = load_v3_candidate_rows(db, user_id=profile.user_id, limit=CANDIDATE_STORAGE_SIZE)
    signature = onboarding_profile_signature(profile)
    selected: list[tuple[Recommendation, str]] = []
    has_current_bundle_feature_only = False
    for row in rows:
        scores = row.source_scores or {}
        if scores.get("model_build_id") != bundle.model.model_build_id:
            continue
        if row.source == "lightfm_v3":
            if scores.get("candidate_snapshot_id") != bundle.candidate_snapshot_id:
                continue
            selected.append((row, "snapshot"))
        elif row.source == "lightfm_v3_feature_only":
            if scores.get("serving_bundle_id") != bundle.bundle_id:
                continue
            has_current_bundle_feature_only = True
            if scores.get("onboarding_profile_signature") != signature:
                continue
            selected.append((row, "feature_only"))
    kinds = {kind for _row, kind in selected}
    if len(kinds) > 1:
        raise ValueError("V3 published candidates mix snapshot and feature-only sources")
    candidates = tuple(
        LongTermCandidate(
            movie_id=int(row.movie_id),
            model_raw_score=float((row.source_scores or {}).get("model_raw_score", row.score)),
            source_rank=int((row.source_scores or {}).get("model_source_rank", row.rank)),
        )
        for row, _kind in selected
    )
    if len({item.movie_id for item in candidates}) != len(candidates):
        raise ValueError("V3 published candidates contain duplicate movies")
    kind = next(iter(kinds), None)
    if kind is None and has_current_bundle_feature_only:
        kind = "feature_only"
    return candidates, kind


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
        blacklisted_movie_ids=frozenset(get_blacklisted_movie_ids(redis, user_id)),
        genre_only_cold_start=genre_only_cold_start,
    )


def _persist_request_diagnostics(
    db: Session,
    *,
    bundle: ServingBundle,
    user_id: int,
    shuffle_seed: str | None,
    policy: PolicyEvaluationResult,
    page: tuple[RankedPolicyCandidate, ...],
    request_path: dict[str, object],
) -> None:
    request_id = uuid.uuid4().hex
    try:
        run = create_run(
            db,
            run_id=request_id,
            engine=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            ontology_build_id=bundle.ontology_build_id,
            run_type="request",
            config_snapshot={
                "request_marker": shuffle_seed,
                "bundle_id": bundle.bundle_id,
                "model_build_id": bundle.model.model_build_id,
                "candidate_snapshot_id": bundle.candidate_snapshot_id,
                "policy": policy_config_snapshot(),
                "request_path": request_path,
                "policy_diagnostics": asdict(policy.diagnostics),
                "policy_rejections": [
                    {
                        "movie_id": rejection.movie_id,
                        "reasons": [reason.value for reason in rejection.reasons],
                    }
                    for rejection in policy.rejections
                ],
            },
        )
        rows = _diagnostic_rows(
            request_id=request_id,
            feed_session_key=shuffle_seed,
            user_id=user_id,
            candidates=policy.candidates,
            stage="candidate_slice",
            bundle=bundle,
        )
        rows.extend(
            _diagnostic_rows(
                request_id=request_id,
                feed_session_key=shuffle_seed,
                user_id=user_id,
                candidates=page,
                stage="final_response",
                bundle=bundle,
            )
        )
        add_recommendation_snapshots(db, rows)
        source_counts: dict[str, int] = {}
        for candidate in policy.candidates:
            source = "+".join(item.value for item in candidate.candidate.sources)
            source_counts[source] = source_counts.get(source, 0) + 1
        mark_run_finished(
            db,
            run,
            status="success",
            processed_user_count=1,
            generated_candidate_count=len(policy.candidates),
            source_counts=source_counts,
            fallback_ratio=(
                sum(
                    CandidateSource.COLD_START in item.candidate.sources
                    for item in policy.candidates
                )
                / len(policy.candidates)
                if policy.candidates
                else 1.0
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("V3 recommendation diagnostics persistence failed", exc_info=True)


def _diagnostic_rows(
    *,
    request_id: str,
    feed_session_key: str | None,
    user_id: int,
    candidates: tuple[RankedPolicyCandidate, ...],
    stage: str,
    bundle: ServingBundle,
) -> list[OntologyRecommendation]:
    return [
        OntologyRecommendation(
            run_id=request_id,
            request_id=request_id,
            feed_session_key=feed_session_key,
            user_id=user_id,
            movie_id=item.movie_id,
            rank=item.rank,
            score=item.score.final_score,
            source="+".join(source.value for source in item.candidate.sources),
            source_scores={
                "serving_bundle_id": bundle.bundle_id,
                "model_build_id": bundle.model.model_build_id,
                "candidate_snapshot_id": bundle.candidate_snapshot_id,
                "score_trace": asdict(item.score),
                "ontology_type_scores": {
                    score.feature.value: {
                        "long_positive": score.long_positive_score,
                        "long_negative": score.long_negative_score,
                        "short_positive": score.short_positive_score,
                        "short_negative": score.short_negative_score,
                    }
                    for score in item.ontology.type_scores
                },
                "candidate_sources": [
                    source.value for source in item.candidate.sources
                ],
            },
            explanation_tags=[
                {
                    "reason_type": reason.reason_type.value,
                    "feature": reason.feature.value if reason.feature else None,
                    "value": reason.value,
                    "ref_ids": list(reason.ref_ids),
                    "is_model_attribution": reason.is_model_attribution,
                }
                for reason in item.reasons
            ],
            ontology_build_id=bundle.ontology_build_id,
            engine_version=ENGINE_VERSION,
            candidate_stage=stage,
        )
        for item in candidates
    ]


def _page_source(candidates: tuple[RankedPolicyCandidate, ...]) -> str:
    if not candidates:
        return "empty"
    sources = {source for item in candidates for source in item.candidate.sources}
    if sources <= {CandidateSource.MODEL}:
        return "v3_model"
    if sources <= {
        CandidateSource.FEATURE_ONLY_MODEL,
        CandidateSource.COLD_START,
        CandidateSource.ONTOLOGY_COLD_ITEM,
    }:
        return "v3_cold_start"
    return "v3_mixed"


def _candidate_source_counts(candidates) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        source = "+".join(item.value for item in candidate.sources)
        counts[source] = counts.get(source, 0) + 1
    return counts
