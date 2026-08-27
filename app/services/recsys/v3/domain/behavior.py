from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from app.services.recsys.v3.config import (
    TRAINING_MISSING_TIMESTAMP_MULTIPLIER,
    TRAINING_OLDER_RECENCY_MULTIPLIER,
    TRAINING_RECENCY_BUCKETS,
)


class SnapshotAction(StrEnum):
    FAVORITE = "favorite"
    WATCHED = "watched"
    SAVED = "saved"
    PINNED = "pinned"
    PASSED = "passed"


@dataclass(frozen=True, slots=True)
class SnapshotSignal:
    user_id: int
    movie_id: int
    action: SnapshotAction
    occurred_at: datetime | None


def append_current_signal(
    signals: list[SnapshotSignal],
    *,
    user_id: int,
    movie_id: int,
    action: SnapshotAction,
    enabled: bool,
    occurred_at: datetime | None,
    data_cutoff_at: datetime,
) -> None:
    if not enabled:
        return
    normalized_at = normalize_optional_datetime(occurred_at)
    if normalized_at is not None and normalized_at > data_cutoff_at:
        return
    signals.append(
        SnapshotSignal(
            user_id=user_id,
            movie_id=movie_id,
            action=action,
            occurred_at=normalized_at,
        )
    )


def recency_multiplier(occurred_at: datetime | None, *, data_cutoff_at: datetime) -> float:
    if occurred_at is None:
        return TRAINING_MISSING_TIMESTAMP_MULTIPLIER
    cutoff_at = normalize_datetime(data_cutoff_at)
    action_at = normalize_datetime(occurred_at)
    age_days = max((cutoff_at - action_at).days, 0)
    for max_days, multiplier in TRAINING_RECENCY_BUCKETS:
        if age_days <= max_days:
            return multiplier
    return TRAINING_OLDER_RECENCY_MULTIPLIER


def normalize_optional_datetime(value: datetime | None) -> datetime | None:
    return normalize_datetime(value) if value is not None else None


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def optional_datetime_sort_value(value: datetime | None) -> datetime:
    return value or datetime.min


def signal_sort_key(signal: SnapshotSignal) -> tuple[int, int, str, datetime]:
    return (
        signal.user_id,
        signal.movie_id,
        signal.action.value,
        optional_datetime_sort_value(signal.occurred_at),
    )
