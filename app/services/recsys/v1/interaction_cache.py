import logging

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.schemas.recsys import InteractionAction, InteractionCreate


logger = logging.getLogger(__name__)

BLACKLIST_ACTIONS = {
    InteractionAction.PASSED,
    InteractionAction.WATCHED,
}


def _blacklist_key(user_id: int) -> str:
    return f"user:{user_id}:movie:blacklist"


def _recent_action_key(user_id: int) -> str:
    return f"user:{user_id}:recent_actions"


# 2026.06.04 김호영
# 사용자 행동을 Redis 최근 행동/blacklist cache에 기록하되 실패해도 DB 저장 성공을 막지 않는다.
def record_interaction_cache(redis: Redis, settings: Settings, payload: InteractionCreate) -> bool:
    try:
        redis.lpush(_recent_action_key(payload.user_id), f"{payload.action.value}:{payload.movie_id}")
        redis.ltrim(_recent_action_key(payload.user_id), 0, settings.REDIS_RECENT_ACTION_LIMIT - 1)

        if payload.action in BLACKLIST_ACTIONS:
            key = _blacklist_key(payload.user_id)
            redis.sadd(key, payload.movie_id)
            redis.expire(key, settings.REDIS_BLACKLIST_TTL_SECONDS)
        return True
    except RedisError:
        logger.warning(
            "redis interaction cache unavailable user_id=%s movie_id=%s action=%s",
            payload.user_id,
            payload.movie_id,
            payload.action,
            exc_info=True,
        )
        return False


# 2026.06.04 김호영
# 추천 조회 시 Redis blacklist를 읽고 실패하면 빈 집합으로 fallback한다.
def get_blacklisted_movie_ids(redis: Redis, user_id: int) -> set[int]:
    try:
        values = redis.smembers(_blacklist_key(user_id))
        return {int(value) for value in values}
    except RedisError:
        logger.warning("redis blacklist unavailable user_id=%s", user_id, exc_info=True)
        return set()


def remove_blacklisted_movie_ids(redis: Redis, user_id: int, movie_ids: set[int]) -> bool:
    if not movie_ids:
        return True
    try:
        redis.srem(_blacklist_key(user_id), *movie_ids)
        return True
    except RedisError:
        logger.warning(
            "redis blacklist cleanup unavailable user_id=%s movie_ids=%s",
            user_id,
            sorted(movie_ids),
            exc_info=True,
        )
        return False
