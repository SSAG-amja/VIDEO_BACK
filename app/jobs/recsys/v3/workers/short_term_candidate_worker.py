from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone

from app.core.redis import get_redis
from app.db.session import SessionLocal
from app.services.recsys.profile_change import (
    acknowledge_short_term_refresh,
    claim_recommendation_profile_refreshes,
    complete_recommendation_profile_refresh,
    enqueue_recommendation_profile_refresh,
    load_pending_short_term_refresh,
    mark_short_term_refresh_eligible,
)
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.domain.schemas import OttFilterMode
from app.services.recsys.v3.serving.serving_bundle import get_active_serving_bundle
from app.services.recsys.v3.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
)
from app.services.recsys.v3.retrieval.short_term_refresh_policy import evaluate_short_term_refresh


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshBatchResult:
    claimed_user_count: int
    refreshed_user_count: int
    insufficient_user_count: int
    deferred_user_count: int
    requeued_user_count: int
    failed_user_count: int
    elapsed_seconds: float


def refresh_dirty_users(*, batch_size: int = 16) -> RefreshBatchResult:
    started = time.monotonic()
    redis = get_redis()
    user_ids = claim_recommendation_profile_refreshes(redis, batch_size)
    if not user_ids:
        return RefreshBatchResult(0, 0, 0, 0, 0, 0, round(time.monotonic() - started, 6))

    now = time.time()
    ready = []
    insufficient = 0
    deferred = 0
    failed = 0
    for user_id in user_ids:
        pending = load_pending_short_term_refresh(redis, user_id, now=now)
        if pending is None:
            failed += 1
            enqueue_recommendation_profile_refresh(redis, user_id, due_at=now + 1.0)
            complete_recommendation_profile_refresh(redis, user_id)
            continue
        decision = evaluate_short_term_refresh(pending, now=now)
        if not decision.eligible:
            insufficient += 1
            complete_recommendation_profile_refresh(redis, user_id)
            continue
        if pending.eligible_at is None:
            mark_short_term_refresh_eligible(redis, user_id, now)
            decision = evaluate_short_term_refresh(
                replace(pending, eligible_at=now),
                now=now,
            )
        if not decision.ready:
            deferred += 1
            enqueue_recommendation_profile_refresh(
                redis,
                user_id,
                due_at=decision.due_at,
            )
            complete_recommendation_profile_refresh(redis, user_id)
            continue
        ready.append(pending)

    bundle = get_active_serving_bundle() if ready else None
    refreshed = 0
    requeued = 0
    for pending in ready:
        user_id = pending.user_id
        try:
            with SessionLocal() as db:
                profile = build_user_runtime_profile(
                    db,
                    user_id=user_id,
                    ontology_build_id=bundle.ontology_build_id,
                    as_of=datetime.now(timezone.utc),
                    model_user_known=bundle.model.user_index(user_id) is not None,
                    ott_mode=OttFilterMode.ALL,
                ).bundle
                retrieve_cached_short_term_candidates(
                    db,
                    redis=redis,
                    ontology_build_id=bundle.ontology_build_id,
                    profile=profile,
                    force_refresh=True,
                )
            if acknowledge_short_term_refresh(redis, user_id, pending.revision):
                refreshed += 1
            else:
                enqueue_recommendation_profile_refresh(redis, user_id, due_at=time.time())
                requeued += 1
            complete_recommendation_profile_refresh(redis, user_id)
        except Exception:
            failed += 1
            enqueue_recommendation_profile_refresh(redis, user_id, due_at=time.time() + 1.0)
            complete_recommendation_profile_refresh(redis, user_id)
            logger.exception("V3 short-term candidate refresh failed user_id=%s", user_id)
    return RefreshBatchResult(
        claimed_user_count=len(user_ids),
        refreshed_user_count=refreshed,
        insufficient_user_count=insufficient,
        deferred_user_count=deferred,
        requeued_user_count=requeued,
        failed_user_count=failed,
        elapsed_seconds=round(time.monotonic() - started, 6),
    )


def run_worker(*, batch_size: int, poll_seconds: float, once: bool) -> None:
    while True:
        result = refresh_dirty_users(batch_size=batch_size)
        if result.claimed_user_count:
            logger.info("V3 short-term refresh batch %s", json.dumps(asdict(result), sort_keys=True))
        if once:
            print(json.dumps(asdict(result), sort_keys=True))
            return
        if not result.claimed_user_count:
            time.sleep(poll_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh dirty V3 short-term candidate caches")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    if args.batch_size <= 0 or args.poll_seconds <= 0:
        raise ValueError("worker batch size and poll seconds must be positive")
    run_worker(
        batch_size=args.batch_size,
        poll_seconds=args.poll_seconds,
        once=args.once,
    )


if __name__ == "__main__":
    main()
