from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from redis import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)

PROFILE_VERSION_TTL_SECONDS = 60 * 60 * 24 * 90
SHORT_TERM_PENDING_TTL_SECONDS = 60 * 60 * 24
PROFILE_REFRESH_LEASE_SECONDS = 60 * 15

V3_SHORT_TERM_SCHEDULED_USERS_KEY = "recsys:v3:short_term:scheduled_users:v2"
V3_SHORT_TERM_PROCESSING_USERS_KEY = "recsys:v3:short_term:processing_users:v2"

_CLAIM_REFRESHES_LUA = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, member in ipairs(expired) do
    redis.call('ZREM', KEYS[2], member)
    redis.call('ZADD', KEYS[1], ARGV[1], member)
end
local claimed = redis.call(
    'ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2]
)
for _, member in ipairs(claimed) do
    redis.call('ZREM', KEYS[1], member)
    redis.call('ZADD', KEYS[2], ARGV[3], member)
end
return claimed
"""

_ACKNOWLEDGE_REFRESH_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current ~= tonumber(ARGV[1]) then
    return 0
end
redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
redis.call('ZREM', KEYS[5], ARGV[2])
redis.call('ZREM', KEYS[6], ARGV[2])
return 1
"""


@dataclass(frozen=True, slots=True)
class PendingShortTermRefresh:
    user_id: int
    revision: int
    positive_movie_weights: tuple[tuple[int, float], ...]
    first_positive_at: float | None
    last_change_at: float | None
    eligible_at: float | None
    force_refresh: bool


def profile_version_key(user_id: int) -> str:
    _validate_user_id(user_id)
    return f"user:{user_id}:recsys:profile_version"


def short_term_revision_key(user_id: int) -> str:
    _validate_user_id(user_id)
    return f"user:{user_id}:v3:short_term:revision"


def short_term_pending_movies_key(user_id: int) -> str:
    _validate_user_id(user_id)
    return f"user:{user_id}:v3:short_term:pending_movies"


def short_term_pending_weights_key(user_id: int) -> str:
    _validate_user_id(user_id)
    return f"user:{user_id}:v3:short_term:pending_weights"


def short_term_pending_meta_key(user_id: int) -> str:
    _validate_user_id(user_id)
    return f"user:{user_id}:v3:short_term:pending_meta"


def mark_recommendation_profile_changed(redis: Redis, user_id: int) -> bool:
    """Version the live profile without requesting candidate regeneration."""
    try:
        pipeline = redis.pipeline(transaction=True)
        pipeline.incr(profile_version_key(user_id))
        pipeline.expire(profile_version_key(user_id), PROFILE_VERSION_TTL_SECONDS)
        pipeline.execute()
        return True
    except RedisError:
        logger.warning(
            "recommendation profile change notification failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def record_short_term_positive_change(
    redis: Redis,
    *,
    user_id: int,
    movie_id: int,
    weight: float,
    occurred_at: datetime | None = None,
) -> bool:
    _validate_user_id(user_id)
    if movie_id <= 0 or weight <= 0:
        raise ValueError("short-term positive movie ID and weight must be positive")
    timestamp = _timestamp(occurred_at)
    try:
        previous = redis.hget(short_term_pending_weights_key(user_id), movie_id)
        retained_weight = max(float(previous or 0.0), float(weight))
        pipeline = redis.pipeline(transaction=True)
        pipeline.incr(profile_version_key(user_id))
        pipeline.expire(profile_version_key(user_id), PROFILE_VERSION_TTL_SECONDS)
        pipeline.incr(short_term_revision_key(user_id))
        pipeline.expire(short_term_revision_key(user_id), PROFILE_VERSION_TTL_SECONDS)
        pipeline.zadd(short_term_pending_movies_key(user_id), {str(movie_id): timestamp})
        pipeline.hset(short_term_pending_weights_key(user_id), str(movie_id), retained_weight)
        pipeline.hset(short_term_pending_meta_key(user_id), mapping={"last_change_at": timestamp})
        pipeline.expire(short_term_pending_movies_key(user_id), SHORT_TERM_PENDING_TTL_SECONDS)
        pipeline.expire(short_term_pending_weights_key(user_id), SHORT_TERM_PENDING_TTL_SECONDS)
        pipeline.expire(short_term_pending_meta_key(user_id), SHORT_TERM_PENDING_TTL_SECONDS)
        pipeline.zadd(V3_SHORT_TERM_SCHEDULED_USERS_KEY, {str(user_id): timestamp})
        pipeline.execute()
        return True
    except (RedisError, TypeError, ValueError):
        logger.warning(
            "short-term positive change notification failed user_id=%s movie_id=%s",
            user_id,
            movie_id,
            exc_info=True,
        )
        return False


def mark_short_term_positive_removed(
    redis: Redis,
    user_id: int,
    *,
    occurred_at: datetime | None = None,
) -> bool:
    """Force a debounced refresh because materialized positive evidence was removed."""
    timestamp = _timestamp(occurred_at)
    try:
        pipeline = redis.pipeline(transaction=True)
        pipeline.incr(profile_version_key(user_id))
        pipeline.expire(profile_version_key(user_id), PROFILE_VERSION_TTL_SECONDS)
        pipeline.incr(short_term_revision_key(user_id))
        pipeline.expire(short_term_revision_key(user_id), PROFILE_VERSION_TTL_SECONDS)
        pipeline.hset(
            short_term_pending_meta_key(user_id),
            mapping={
                "force_refresh": "1",
                "last_change_at": timestamp,
                "eligible_at": timestamp,
            },
        )
        pipeline.expire(short_term_pending_meta_key(user_id), SHORT_TERM_PENDING_TTL_SECONDS)
        pipeline.zadd(V3_SHORT_TERM_SCHEDULED_USERS_KEY, {str(user_id): timestamp})
        pipeline.execute()
        return True
    except RedisError:
        logger.warning(
            "short-term positive removal notification failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def get_recommendation_profile_version(redis: Redis, user_id: int) -> int | None:
    return _read_int(redis, profile_version_key(user_id), "recommendation profile")


def get_short_term_revision(redis: Redis, user_id: int) -> int | None:
    return _read_int(redis, short_term_revision_key(user_id), "short-term profile")


def enqueue_recommendation_profile_refresh(
    redis: Redis,
    user_id: int,
    *,
    due_at: float | None = None,
    force: bool = False,
) -> bool:
    timestamp = time.time()
    try:
        pipeline = redis.pipeline(transaction=True)
        if force:
            pipeline.incr(short_term_revision_key(user_id))
            pipeline.expire(short_term_revision_key(user_id), PROFILE_VERSION_TTL_SECONDS)
            pipeline.hset(
                short_term_pending_meta_key(user_id),
                mapping={
                    "force_refresh": "1",
                    "last_change_at": timestamp - 30.0,
                    "eligible_at": timestamp - 120.0,
                },
            )
            pipeline.expire(short_term_pending_meta_key(user_id), SHORT_TERM_PENDING_TTL_SECONDS)
        pipeline.zadd(
            V3_SHORT_TERM_SCHEDULED_USERS_KEY,
            {str(user_id): float(due_at if due_at is not None else timestamp)},
        )
        pipeline.execute()
        return True
    except RedisError:
        logger.warning(
            "recommendation profile refresh enqueue failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def claim_recommendation_profile_refreshes(redis: Redis, limit: int) -> tuple[int, ...]:
    if limit <= 0:
        raise ValueError("recommendation profile refresh claim limit must be positive")
    try:
        now = time.time()
        values = redis.eval(
            _CLAIM_REFRESHES_LUA,
            2,
            V3_SHORT_TERM_SCHEDULED_USERS_KEY,
            V3_SHORT_TERM_PROCESSING_USERS_KEY,
            now,
            limit,
            now + PROFILE_REFRESH_LEASE_SECONDS,
        ) or ()
        return tuple(sorted(int(value) for value in values))
    except (RedisError, TypeError, ValueError):
        logger.warning("recommendation profile refresh claim failed", exc_info=True)
        return ()


def load_pending_short_term_refresh(
    redis: Redis,
    user_id: int,
    *,
    now: float | None = None,
) -> PendingShortTermRefresh | None:
    current_time = float(now if now is not None else time.time())
    cutoff = current_time - SHORT_TERM_PENDING_TTL_SECONDS
    try:
        movie_key = short_term_pending_movies_key(user_id)
        weight_key = short_term_pending_weights_key(user_id)
        expired = redis.zrangebyscore(movie_key, "-inf", cutoff)
        if expired:
            pipeline = redis.pipeline(transaction=True)
            pipeline.zrem(movie_key, *expired)
            pipeline.hdel(weight_key, *expired)
            pipeline.execute()
        movie_rows = redis.zrange(movie_key, 0, -1, withscores=True)
        movie_ids = [str(movie_id) for movie_id, _score in movie_rows]
        weights = redis.hmget(weight_key, movie_ids) if movie_ids else []
        positive_weights = tuple(
            sorted(
                (int(movie_id), float(weight))
                for (movie_id, _score), weight in zip(movie_rows, weights, strict=True)
                if weight is not None
            )
        )
        meta = redis.hgetall(short_term_pending_meta_key(user_id))
        revision = get_short_term_revision(redis, user_id)
        if revision is None:
            return None
        first_positive_at = min((float(score) for _movie, score in movie_rows), default=None)
        return PendingShortTermRefresh(
            user_id=user_id,
            revision=revision,
            positive_movie_weights=positive_weights,
            first_positive_at=first_positive_at,
            last_change_at=_optional_float(meta.get("last_change_at")),
            eligible_at=_optional_float(meta.get("eligible_at")),
            force_refresh=meta.get("force_refresh") == "1",
        )
    except (RedisError, TypeError, ValueError):
        logger.warning("pending short-term refresh unavailable user_id=%s", user_id, exc_info=True)
        return None


def mark_short_term_refresh_eligible(redis: Redis, user_id: int, eligible_at: float) -> bool:
    try:
        redis.hsetnx(short_term_pending_meta_key(user_id), "eligible_at", eligible_at)
        return True
    except RedisError:
        logger.warning("short-term eligibility mark failed user_id=%s", user_id, exc_info=True)
        return False


def acknowledge_short_term_refresh(redis: Redis, user_id: int, revision: int) -> bool:
    try:
        result = redis.eval(
            _ACKNOWLEDGE_REFRESH_LUA,
            6,
            short_term_revision_key(user_id),
            short_term_pending_movies_key(user_id),
            short_term_pending_weights_key(user_id),
            short_term_pending_meta_key(user_id),
            V3_SHORT_TERM_SCHEDULED_USERS_KEY,
            V3_SHORT_TERM_PROCESSING_USERS_KEY,
            revision,
            user_id,
        )
        return bool(result)
    except RedisError:
        logger.warning("short-term refresh acknowledgement failed user_id=%s", user_id, exc_info=True)
        return False


def complete_recommendation_profile_refresh(redis: Redis, user_id: int) -> bool:
    try:
        redis.zrem(V3_SHORT_TERM_PROCESSING_USERS_KEY, user_id)
        return True
    except RedisError:
        logger.warning(
            "recommendation profile refresh completion failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return False


def _read_int(redis: Redis, key: str, label: str) -> int | None:
    try:
        value = redis.get(key)
        return int(value) if value is not None else 0
    except (RedisError, TypeError, ValueError):
        logger.warning("%s version unavailable key=%s", label, key, exc_info=True)
        return None


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return time.time()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None


def _validate_user_id(user_id: int) -> None:
    if user_id <= 0:
        raise ValueError("recommendation profile user ID must be positive")
