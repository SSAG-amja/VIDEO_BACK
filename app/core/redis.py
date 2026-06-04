from redis import Redis

from app.core.config import settings


# 2026.06.04 김호영
# 추천 blacklist와 최근 행동 cache에 사용할 Redis client를 생성한다.
def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)
