import asyncio
import logging

from sqlalchemy import update
from sqlalchemy.future import select

from app.jobs.video_updater.fetchers.api_fetcher import TMDBApiFetcher
from app.models.people import People

logger = logging.getLogger("PERSON_SYNC")


class PersonSynchronizer:
    def __init__(self, db_session):
        self.db = db_session
        self.api_fetcher = TMDBApiFetcher()

    # 2026.05.23 김호영
    # 전체 person dump 대신 DB에 존재하는 영화 관련 인물만 Change API 기준으로 보조 동기화한다.
    # 26.05.17 김광원
    # people은 movies credits에서 발견된 배우/감독만 관리한다.
    # TMDB person dump 전체는 영화 범위를 벗어난 TV/드라마 인물이 많아 사용하지 않는다.
    async def sync_people(self, aio_session, date_str: str, start_date: str, end_date: str):
        logger.info("영화 관련 인물 보조 동기화 시작...")

        result = await self.db.execute(select(People.tmdb_id).where(People.tmdb_id.is_not(None)))
        db_ids = {row.tmdb_id for row in result.all()}
        if not db_ids:
            logger.info("people 테이블에 보조 동기화 대상이 없습니다.")
            return

        await self._update_changed_people(aio_session, start_date, end_date, db_ids)

    # 2026.05.23 김호영
    # Change API 결과와 현재 DB 인물 id의 교집합만 상세 조회해 이름을 갱신한다.
    # 26.05.17 김광원
    # Change API에 포함된 기존 영화 관련 인물의 최신 이름만 반영한다.
    async def _update_changed_people(self, aio_session, start_date: str, end_date: str, db_ids: set):
        changed_ids_from_api = set()
        page = 1

        while True:
            change_data = await self.api_fetcher.fetch_changes(
                aio_session,
                start_date,
                end_date,
                page=page,
                endpoint="/person/changes",
            )

            if not change_data:
                break

            changed_ids_from_api.update(item["id"] for item in change_data.get("results", []))

            total_pages = change_data.get("total_pages", 1)
            if page >= total_pages:
                break

            page += 1

        target_ids = list(db_ids.intersection(changed_ids_from_api))
        if not target_ids:
            logger.info("업데이트 대상 영화 관련 인물이 없습니다.")
            return

        logger.info("총 %s명의 영화 관련 인물 정보 비동기 업데이트 시작...", len(target_ids))
        tasks = [
            self.api_fetcher.fetch_with_retry(
                aio_session,
                f"{self.api_fetcher.base_url}/person/{person_id}",
                failure_context={"entity_type": "person", "entity_id": person_id},
            )
            for person_id in target_ids
        ]
        results = await asyncio.gather(*tasks)

        updated_count = 0
        missing_count = 0
        for person_data in results:
            if not person_data:
                missing_count += 1
                continue

            await self.db.execute(
                update(People)
                .where(People.tmdb_id == person_data["id"])
                .values(name=person_data.get("name"))
            )
            updated_count += 1

        if missing_count:
            logger.warning(
                "%s명의 영화 관련 인물은 TMDB 상세 조회에 실패했습니다. movie credits 재처리 시 새 tmdb_id가 발견되면 별도 row로 보강됩니다.",
                missing_count,
            )

        logger.info("%s명의 영화 관련 인물 정보 업데이트 완료.", updated_count)
