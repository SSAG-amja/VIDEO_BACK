import gzip
import logging
import os
from datetime import datetime, timedelta

import aiohttp
import ijson

from app.core.config import settings

logger = logging.getLogger("DUMP_FETCHER")
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=15, sock_read=300)


class TMDBDumpFetcher:
    def __init__(self):
        self.download_dir = settings.DOWNLOAD_DIR
        os.makedirs(self.download_dir, exist_ok=True)

    # 26.05.17 김광원
    # TMDB export dump 파일을 다운로드하고 실패 시 API 기반 흐름을 계속하게 한다.
    async def download_dump(self, dump_type: str, date_str: str) -> str | None:
        for target_date_str in self._get_candidate_dates(date_str):
            file_path = await self._download_dump_by_date(dump_type, target_date_str)
            if file_path:
                return file_path

        return None

    # 26.05.18 김광원
    # TMDB dump 생성 지연에 대비해 요청일과 이전 날짜 후보를 만든다.
    def _get_candidate_dates(self, date_str: str) -> list[str]:
        try:
            target_date = datetime.strptime(date_str, "%m_%d_%Y")
        except ValueError:
            return [date_str]

        return [
            (target_date - timedelta(days=offset)).strftime("%m_%d_%Y")
            for offset in range(3)
        ]

    # 26.05.18 김광원
    # 특정 날짜의 TMDB export dump 파일을 다운로드한다.
    async def _download_dump_by_date(self, dump_type: str, date_str: str) -> str | None:
        file_name = f"{dump_type}_{date_str}.json.gz"
        save_path = os.path.join(self.download_dir, file_name)
        temp_path = f"{save_path}.part"
        url = f"https://files.tmdb.org/p/exports/{file_name}"

        if os.path.exists(save_path):
            logger.info("이미 파일이 존재하여 스킵합니다: %s", file_name)
            return save_path

        logger.info("덤프 파일 다운로드 시작: %s", url)

        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning(
                            "덤프 다운로드 실패 (HTTP %s, file=%s) - 이전 날짜 후보가 있으면 계속 시도합니다.",
                            response.status,
                            file_name,
                        )
                        return None

                    with open(temp_path, "wb") as file:
                        while True:
                            chunk = await response.content.read(1024 * 1024)
                            if not chunk:
                                break
                            file.write(chunk)
            os.replace(temp_path, save_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logger.warning(
                "덤프 다운로드 중 네트워크 예외 발생 (%s) - dump 기반 단계는 건너뛰고 API 기반 흐름을 계속합니다.",
                str(exc),
            )
            return None

        logger.info("다운로드 완료: %s", save_path)
        return save_path

    # 26.05.17 김광원
    # gzip JSON dump를 메모리에 올리지 않고 순차 파싱한다.
    def get_dump_iterator(self, file_path: str):
        if not file_path or not os.path.exists(file_path):
            logger.error("파일을 찾을 수 없습니다: %s", file_path)
            return

        with gzip.open(file_path, "rb") as file:
            yield from ijson.items(file, "", multiple_values=True)
