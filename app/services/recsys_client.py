import logging
from typing import Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RecommendationMode = Literal["all", "subscribed_only"]
InteractionAction = Literal["passed", "pinned", "watched"]


# 2026.05.23 김호영
# VIDEO_BACK에서 사용할 VIDEO_RECSYS API base url을 정규화한다.
def _base_url() -> str:
    return settings.RECSYS_BASE_URL.rstrip("/")


# 2026.05.23 김호영
# VIDEO_RECSYS 추천 API에서 내부 movie id 목록을 받아오고 실패 시 None을 반환한다.
def get_recommendation_movie_ids(
    user_id: int,
    *,
    mode: RecommendationMode = "all",
    limit: int = 100,
    offset: int = 0,
) -> list[int] | None:
    """Return internal BACK movie ids from VIDEO_RECSYS, or None on service failure."""
    url = f"{_base_url()}/api/v1/recommendations/{user_id}"
    try:
        with httpx.Client(timeout=settings.RECSYS_TIMEOUT_SECONDS) as client:
            response = client.get(url, params={"mode": mode, "limit": limit, "offset": offset})
            if response.status_code == 404:
                logger.warning("RECSYS recommendations user not found user_id=%s", user_id)
                return []
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("RECSYS recommendations request failed user_id=%s error=%s", user_id, exc)
        return None

    movie_ids = payload.get("movie_ids")
    if not isinstance(movie_ids, list):
        logger.warning("RECSYS recommendations response has invalid movie_ids user_id=%s", user_id)
        return None

    return [movie_id for movie_id in movie_ids if isinstance(movie_id, int)]


# 2026.05.23 김호영
# 온보딩 완료 사용자의 콜드스타트 추천 풀 생성을 VIDEO_RECSYS에 요청한다.
def create_cold_start_pool(user_id: int) -> bool:
    url = f"{_base_url()}/api/v1/cold-start"
    try:
        timeout = max(settings.RECSYS_TIMEOUT_SECONDS, 10.0)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json={"user_id": user_id})
            response.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        logger.warning("RECSYS cold-start request failed user_id=%s error=%s", user_id, exc)
        return False


# 2026.05.23 김호영
# 사용자 pin/pass/watch 행동을 VIDEO_RECSYS 최근 행동 캐시에 전달한다.
def record_interaction(user_id: int, movie_id: int, action: InteractionAction) -> bool:
    url = f"{_base_url()}/api/v1/interactions"
    try:
        with httpx.Client(timeout=settings.RECSYS_TIMEOUT_SECONDS) as client:
            response = client.post(url, json={"user_id": user_id, "movie_id": movie_id, "action": action})
            response.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        logger.warning(
            "RECSYS interaction request failed user_id=%s movie_id=%s action=%s error=%s",
            user_id,
            movie_id,
            action,
            exc,
        )
        return False
