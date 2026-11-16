from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.crud.recsys.recommendations import (
    load_v4_candidate_rows,
    replace_user_recommendation_rows,
)
from app.models.recommendations import Recommendation
from app.services.recsys.v4.domain.onboarding import onboarding_feature_signature
from app.services.recsys.v4.domain.schemas import UserProfileBundle
from app.services.recsys.v4.retrieval.retrieval_schemas import LongTermCandidate
from app.services.recsys.v4.serving.serving_bundle import ServingBundle
from app.services.recsys.v4.config import (
    CANDIDATE_STORAGE_SIZE,
    INITIAL_CANDIDATE_FILTER_POLICY_VERSION,
)


logger = logging.getLogger(__name__)


def load_published_candidates(
    db: Session,
    *,
    bundle: ServingBundle,
    profile: UserProfileBundle,
) -> tuple[tuple[LongTermCandidate, ...], str | None]:
    """Read candidates produced by the active V4 model and snapshot."""
    rows = load_v4_candidate_rows(
        db,
        user_id=profile.user_id,
        limit=CANDIDATE_STORAGE_SIZE,
    )
    signature = _onboarding_profile_signature(profile)
    selected: list[tuple[Recommendation, str]] = []
    has_current_bundle_feature_only = False
    for row in rows:
        scores = row.source_scores or {}
        if scores.get("model_build_id") != bundle.model.model_build_id:
            continue
        if (
            scores.get("initial_candidate_filter_policy_version")
            != INITIAL_CANDIDATE_FILTER_POLICY_VERSION
        ):
            continue
        if row.source == "lightfm_v4":
            if scores.get("candidate_snapshot_id") != bundle.candidate_snapshot_id:
                continue
            selected.append((row, "snapshot"))
        elif row.source == "lightfm_v4_feature_only":
            if scores.get("serving_bundle_id") != bundle.bundle_id:
                continue
            has_current_bundle_feature_only = True
            if scores.get("onboarding_profile_signature") != signature:
                continue
            selected.append((row, "feature_only"))

    kinds = {kind for _row, kind in selected}
    if len(kinds) > 1:
        raise ValueError("V4 candidates mix snapshot and feature-only sources")
    candidates = tuple(
        LongTermCandidate(
            movie_id=int(row.movie_id),
            model_raw_score=float(
                (row.source_scores or {}).get("model_raw_score", row.score)
            ),
            source_rank=int(
                (row.source_scores or {}).get("model_source_rank", row.rank)
            ),
        )
        for row, _kind in selected
    )
    if len({item.movie_id for item in candidates}) != len(candidates):
        raise ValueError("V4 candidates contain duplicate movies")
    kind = next(iter(kinds), None)
    if kind is None and has_current_bundle_feature_only:
        kind = "feature_only"
    return candidates, kind


def persist_feature_only_candidates(
    db: Session,
    *,
    bundle: ServingBundle,
    profile: UserProfileBundle,
    candidates: tuple[LongTermCandidate, ...],
    suppress_errors: bool,
) -> bool:
    signature = _onboarding_profile_signature(profile)
    rows = [
        Recommendation(
            user_id=profile.user_id,
            movie_id=item.movie_id,
            engine="v4",
            score=item.model_raw_score,
            rank=item.source_rank,
            source="lightfm_v4_feature_only",
            source_scores={
                "candidate_kind": "feature_only",
                "model_raw_score": item.model_raw_score,
                "model_source_rank": item.source_rank,
                "model_build_id": bundle.model.model_build_id,
                "serving_bundle_id": bundle.bundle_id,
                "onboarding_profile_signature": signature,
                "initial_candidate_filter_policy_version": (
                    INITIAL_CANDIDATE_FILTER_POLICY_VERSION
                ),
                "engine": "v4",
            },
        )
        for item in candidates
    ]
    try:
        replace_user_recommendation_rows(db, profile.user_id, rows, engine="v4")
        db.commit()
        return True
    except Exception:
        db.rollback()
        if not suppress_errors:
            raise
        logger.warning(
            "V4 feature-only candidate persistence failed user_id=%s",
            profile.user_id,
            exc_info=True,
        )
        return False


def _onboarding_profile_signature(profile: UserProfileBundle) -> str:
    return onboarding_feature_signature(
        genre_ids=profile.onboarding.genre_ids,
        favorite_movie_ids=profile.onboarding.favorite_movie_ids,
    )
