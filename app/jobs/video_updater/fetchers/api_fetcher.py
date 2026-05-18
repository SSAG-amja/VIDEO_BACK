import asyncio
import json
import logging
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger("API_FETCHER")


class TMDBApiFetcher:
    def __init__(self):
        self.api_key = settings.TMDB_API_KEY
        self.base_url = "https://api.themoviedb.org/3"
        self.params = {"api_key": self.api_key, "language": "en-US"}
        self.semaphore = asyncio.Semaphore(40)
        self.error_log_path = "failed_sync_ids.jsonl"

    # 26.05.17 김광원
    # TMDB API 호출을 재시도하고 실패한 엔터티를 기록한다.
    async def _fetch_with_retry(self, session, url, params=None, retries=3, failure_context=None):
        current_params = self.params.copy()
        if params:
            current_params.update(params)

        for attempt in range(1, retries + 1):
            async with self.semaphore:
                try:
                    async with session.get(url, params=current_params, timeout=10) as response:
                        if response.status == 200:
                            return await response.json()
                        if response.status == 404:
                            logger.info("HTTP 404 for %s - skipping deleted or inaccessible entity", url)
                            return None
                        if response.status in (400, 401, 403):
                            logger.warning("HTTP %s for %s - non-retriable request", response.status, url)
                            self._log_error(url, failure_context=failure_context)
                            return None
                        if response.status == 429:
                            wait_time = int(response.headers.get("Retry-After", 1))
                            await asyncio.sleep(wait_time)
                        else:
                            logger.warning("[Attempt %s] HTTP %s for %s", attempt, response.status, url)
                except Exception as exc:
                    logger.error("[Attempt %s] Error fetching %s: %s", attempt, url, str(exc))

            if attempt < retries:
                await asyncio.sleep(2**attempt)

        self._log_error(url, failure_context=failure_context)
        return None

    # 26.05.17 김광원
    # 외부 모듈에서 공통 재시도 로직을 호출한다.
    async def fetch_with_retry(self, session, url, params=None, retries=3, failure_context=None):
        return await self._fetch_with_retry(
            session,
            url,
            params=params,
            retries=retries,
            failure_context=failure_context,
        )

    # 26.05.17 김광원
    # 실패한 API 호출 정보를 jsonl 파일에 남긴다.
    def _log_error(self, url, failure_context=None):
        payload = {
            "timestamp": datetime.now().isoformat(),
            "url": url,
        }
        if failure_context:
            payload.update(failure_context)

        with open(self.error_log_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # 26.05.17 김광원
    # TMDB 영화 장르 목록을 가져온다.
    async def fetch_genres(self, session, language=None):
        url = f"{self.base_url}/genre/movie/list"
        params = {"language": language} if language else None
        data = await self._fetch_with_retry(session, url, params=params)
        return data.get("genres", []) if data else []

    # 26.05.17 김광원
    # 한국 지역 OTT provider 목록을 가져온다.
    async def fetch_otts(self, session, language=None):
        url = f"{self.base_url}/watch/providers/movie"
        params = {"watch_region": "KR"}
        if language:
            params["language"] = language
        data = await self._fetch_with_retry(session, url, params=params)
        return data.get("results", []) if data else []

    # 26.05.17 김광원
    # 영화 상세와 매핑에 필요한 부가 응답을 함께 가져온다.
    async def fetch_movie_details(self, session, movie_id, language=None):
        url = f"{self.base_url}/movie/{movie_id}"
        params = {"append_to_response": "credits,keywords,watch/providers,release_dates"}
        if language:
            params["language"] = language
        return await self._fetch_with_retry(
            session,
            url,
            params=params,
            failure_context={"entity_type": "movie", "entity_id": movie_id},
        )

    # 26.05.17 김광원
    # TMDB 변경분 API에서 대상 엔터티 id 목록을 가져온다.
    async def fetch_changes(self, session, start_date, end_date, page=1, endpoint="/movie/changes"):
        url = f"{self.base_url}{endpoint}"
        params = {"start_date": start_date, "end_date": end_date, "page": page}
        return await self._fetch_with_retry(session, url, params=params)
