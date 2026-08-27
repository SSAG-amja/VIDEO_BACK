from __future__ import annotations

import json
import time
from dataclasses import replace

from app.core.redis import get_redis
from app.services.recsys.profile_change import (
    V3_SHORT_TERM_PROCESSING_USERS_KEY,
    V3_SHORT_TERM_SCHEDULED_USERS_KEY,
    claim_recommendation_profile_refreshes,
    complete_recommendation_profile_refresh,
    load_pending_short_term_refresh,
    mark_recommendation_profile_changed,
    mark_short_term_positive_removed,
    mark_short_term_refresh_eligible,
    profile_version_key,
    record_short_term_positive_change,
    short_term_pending_meta_key,
    short_term_pending_movies_key,
    short_term_pending_weights_key,
    short_term_revision_key,
)
from app.services.recsys.v3.retrieval.short_term_refresh_policy import evaluate_short_term_refresh


DIAGNOSTIC_USER_ID = 2_147_483_000


def main() -> None:
    redis = get_redis()
    _cleanup(redis)
    try:
        mark_recommendation_profile_changed(redis, DIAGNOSTIC_USER_ID)
        passed_scheduled = redis.zscore(
            V3_SHORT_TERM_SCHEDULED_USERS_KEY,
            DIAGNOSTIC_USER_ID,
        )
        if passed_scheduled is not None:
            raise RuntimeError("generic negative/filter change unexpectedly scheduled a refresh")

        record_short_term_positive_change(
            redis,
            user_id=DIAGNOSTIC_USER_ID,
            movie_id=101,
            weight=1.0,
        )
        _claim_exact(redis)
        one_action = load_pending_short_term_refresh(redis, DIAGNOSTIC_USER_ID)
        one_decision = evaluate_short_term_refresh(one_action, now=time.time())
        if one_decision.eligible:
            raise RuntimeError("one positive action unexpectedly reached refresh threshold")
        complete_recommendation_profile_refresh(redis, DIAGNOSTIC_USER_ID)

        record_short_term_positive_change(
            redis,
            user_id=DIAGNOSTIC_USER_ID,
            movie_id=202,
            weight=1.0,
        )
        _claim_exact(redis)
        two_actions = load_pending_short_term_refresh(redis, DIAGNOSTIC_USER_ID)
        now = time.time()
        two_decision = evaluate_short_term_refresh(two_actions, now=now)
        if not two_decision.eligible or two_decision.ready:
            raise RuntimeError("two strong positives did not enter the debounce state")
        mark_short_term_refresh_eligible(redis, DIAGNOSTIC_USER_ID, now)
        ready_decision = evaluate_short_term_refresh(
            replace(two_actions, eligible_at=now),
            now=now + 30.0,
        )
        if not ready_decision.ready:
            raise RuntimeError("eligible refresh was not ready after the debounce interval")
        complete_recommendation_profile_refresh(redis, DIAGNOSTIC_USER_ID)

        _cleanup(redis)
        mark_short_term_positive_removed(redis, DIAGNOSTIC_USER_ID)
        _claim_exact(redis)
        removed = load_pending_short_term_refresh(redis, DIAGNOSTIC_USER_ID)
        removed_decision = evaluate_short_term_refresh(removed, now=time.time())
        if not removed_decision.eligible or removed_decision.reason != "forced_positive_removal":
            raise RuntimeError("positive removal did not force a debounced refresh")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "generic_filter_change_scheduled": False,
                    "one_positive_eligible": one_decision.eligible,
                    "two_strong_positive_eligible": two_decision.eligible,
                    "two_strong_positive_ready_immediately": two_decision.ready,
                    "ready_after_30_seconds": ready_decision.ready,
                    "positive_removal_forced": removed_decision.eligible,
                },
                sort_keys=True,
            )
        )
    finally:
        _cleanup(redis)


def _claim_exact(redis) -> None:
    claimed = claim_recommendation_profile_refreshes(redis, 1)
    if claimed != (DIAGNOSTIC_USER_ID,):
        raise RuntimeError(f"unexpected refresh claim: {claimed}")


def _cleanup(redis) -> None:
    redis.unlink(
        profile_version_key(DIAGNOSTIC_USER_ID),
        short_term_revision_key(DIAGNOSTIC_USER_ID),
        short_term_pending_movies_key(DIAGNOSTIC_USER_ID),
        short_term_pending_weights_key(DIAGNOSTIC_USER_ID),
        short_term_pending_meta_key(DIAGNOSTIC_USER_ID),
    )
    redis.zrem(V3_SHORT_TERM_SCHEDULED_USERS_KEY, DIAGNOSTIC_USER_ID)
    redis.zrem(V3_SHORT_TERM_PROCESSING_USERS_KEY, DIAGNOSTIC_USER_ID)


if __name__ == "__main__":
    main()
