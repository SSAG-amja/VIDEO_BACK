from __future__ import annotations

from dataclasses import dataclass

from app.services.recsys.profile_change import PendingShortTermRefresh


SHORT_TERM_COLLECTION_WINDOW_SECONDS = 60 * 60 * 24
SHORT_TERM_DEBOUNCE_SECONDS = 30.0
SHORT_TERM_MAX_WAIT_SECONDS = 120.0
SHORT_TERM_MIN_DISTINCT_POSITIVE_MOVIES = 3
SHORT_TERM_STRONG_MIN_DISTINCT_MOVIES = 2
SHORT_TERM_STRONG_MIN_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class ShortTermRefreshDecision:
    eligible: bool
    ready: bool
    due_at: float | None
    reason: str
    distinct_positive_movie_count: int
    positive_weight_sum: float


def evaluate_short_term_refresh(
    pending: PendingShortTermRefresh,
    *,
    now: float,
) -> ShortTermRefreshDecision:
    distinct_count = len(pending.positive_movie_weights)
    weight_sum = sum(weight for _movie_id, weight in pending.positive_movie_weights)
    accumulated = (
        distinct_count >= SHORT_TERM_MIN_DISTINCT_POSITIVE_MOVIES
        or (
            distinct_count >= SHORT_TERM_STRONG_MIN_DISTINCT_MOVIES
            and weight_sum >= SHORT_TERM_STRONG_MIN_WEIGHT
        )
    )
    eligible = pending.force_refresh or accumulated
    if not eligible:
        return ShortTermRefreshDecision(
            eligible=False,
            ready=False,
            due_at=None,
            reason="insufficient_positive_evidence",
            distinct_positive_movie_count=distinct_count,
            positive_weight_sum=weight_sum,
        )

    eligible_at = pending.eligible_at if pending.eligible_at is not None else now
    last_change_at = pending.last_change_at if pending.last_change_at is not None else now
    due_at = min(
        last_change_at + SHORT_TERM_DEBOUNCE_SECONDS,
        eligible_at + SHORT_TERM_MAX_WAIT_SECONDS,
    )
    return ShortTermRefreshDecision(
        eligible=True,
        ready=now >= due_at,
        due_at=due_at,
        reason=("forced_positive_removal" if pending.force_refresh else "positive_threshold"),
        distinct_positive_movie_count=distinct_count,
        positive_weight_sum=weight_sum,
    )
