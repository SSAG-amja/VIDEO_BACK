import asyncio
import logging

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from app.jobs.video_updater.fetchers.api_fetcher import TMDBApiFetcher
from app.jobs.video_updater.fetchers.dump_fetcher import TMDBDumpFetcher
from app.jobs.video_updater.utils.compare_utils import build_normalized_lookup, normalize_compare_text
from app.models.people import People

logger = logging.getLogger("PERSON_SYNC")


class PersonSynchronizer:
    def __init__(self, db_session):
        self.db = db_session
        self.api_fetcher = TMDBApiFetcher()
        self.dump_fetcher = TMDBDumpFetcher()

    # 26.05.17 김광원
    # TMDB dump와 change API를 함께 사용해 people 테이블을 동기화한다.
    async def sync_people(self, aio_session, date_str: str, start_date: str, end_date: str):
        logger.info("인물 하이브리드 동기화 시작...")

        result = await self.db.execute(select(People.id, People.tmdb_id, People.name))
        rows = result.all()
        db_ids = {row.tmdb_id for row in rows}
        db_name_lookup = build_normalized_lookup(rows, "name")

        if not db_ids:
            await self._load_initial_people(date_str)
            result = await self.db.execute(select(People.id, People.tmdb_id, People.name))
            rows = result.all()
            db_ids = {row.tmdb_id for row in rows}
            db_name_lookup = build_normalized_lookup(rows, "name")

        db_ids = await self._delete_removed_people(aio_session, date_str, db_ids, db_name_lookup)
        await self._update_changed_people(aio_session, start_date, end_date, db_ids)

    # 26.05.17 김광원
    # people 테이블이 비어 있을 때 dump 기반 초기 적재를 수행한다.
    async def _load_initial_people(self, date_str: str):
        logger.info("people 테이블이 비어 있어 dump 기반 초기 적재를 수행합니다.")
        dump_file = await self.dump_fetcher.download_dump("person_ids", date_str)
        if not dump_file:
            logger.warning(
                "person dump 파일이 없어 초기 적재를 건너뜁니다. "
                "이 경우 이후 movie 상세 동기화 단계에서 필요한 인물만 점진적으로 보강됩니다."
            )
            return

        pending_people = []
        chunk_size = 5000
        for item in self.dump_fetcher.get_dump_iterator(dump_file):
            person_name = item.get("name") or f"person_{item['id']}"
            pending_people.append(
                {
                    "tmdb_id": item["id"],
                    "name": person_name,
                    "name_ko": person_name,
                }
            )

            if len(pending_people) >= chunk_size:
                stmt = insert(People).values(pending_people).on_conflict_do_nothing(index_elements=["tmdb_id"])
                await self.db.execute(stmt)
                pending_people = []

        if pending_people:
            stmt = insert(People).values(pending_people).on_conflict_do_nothing(index_elements=["tmdb_id"])
            await self.db.execute(stmt)

        logger.info("people 테이블 초기 적재 완료.")

    # 26.05.17 김광원
    # dump에서 제거된 인물을 DB에서도 삭제하고 이름 기반 tmdb_id 보정을 수행한다.
    async def _delete_removed_people(self, aio_session, date_str: str, db_ids: set, db_name_lookup: dict) -> set:
        dump_file = await self.dump_fetcher.download_dump("person_ids", date_str)
        if not dump_file:
            return db_ids

        dump_ids = set()
        for item in self.dump_fetcher.get_dump_iterator(dump_file):
            dump_id = item["id"]
            dump_ids.add(dump_id)

            if dump_id in db_ids:
                continue

            matched_row = db_name_lookup.get(normalize_compare_text(item.get("name")))
            if not matched_row:
                continue

            detail = await self.api_fetcher.fetch_with_retry(
                aio_session,
                f"{self.api_fetcher.base_url}/person/{dump_id}",
                failure_context={"entity_type": "person", "entity_id": dump_id},
            )

            if not detail or normalize_compare_text(detail.get("name")) != normalize_compare_text(matched_row.name):
                continue

            await self.db.execute(update(People).where(People.id == matched_row.id).values(tmdb_id=dump_id))
            db_ids.discard(matched_row.tmdb_id)
            db_ids.add(dump_id)

        delete_ids = db_ids - dump_ids
        if delete_ids:
            await self.db.execute(delete(People).where(People.tmdb_id.in_(list(delete_ids))))
            logger.info("%s명의 삭제된 인물 정리 완료.", len(delete_ids))
            return db_ids - delete_ids

        return db_ids

    # 26.05.17 김광원
    # Change API에 포함된 기존 인물의 최신 이름을 반영한다.
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
            logger.info("업데이트 대상 인물이 없습니다.")
            return

        logger.info("총 %s명의 인물 정보 비동기 업데이트 시작...", len(target_ids))
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
        for person_data in results:
            if not person_data:
                continue

            await self.db.execute(
                update(People)
                .where(People.tmdb_id == person_data["id"])
                .values(name=person_data.get("name"))
            )
            updated_count += 1

        logger.info("%s명의 인물 정보 업데이트 완료.", updated_count)
