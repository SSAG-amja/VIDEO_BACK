import logging
from types import SimpleNamespace

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from app.jobs.video_updater.fetchers.dump_fetcher import TMDBDumpFetcher
from app.jobs.video_updater.utils.compare_utils import build_normalized_lookup, normalize_compare_text
from app.models.keyword import Keyword

logger = logging.getLogger("KEYWORD_SYNC")


class KeywordSynchronizer:
    def __init__(self, db_session):
        self.db = db_session
        self.dump_fetcher = TMDBDumpFetcher()

    # 26.05.17 김광원
    # TMDB keyword dump를 기준으로 keywords 테이블을 일괄 동기화한다.
    async def sync_keywords(self, date_str: str):
        logger.info("키워드 동기화 시작 (덤프 기반)...")

        file_path = await self.dump_fetcher.download_dump("keyword_ids", date_str)
        if not file_path:
            logger.warning("키워드 덤프 파일을 사용할 수 없어 이번 실행에서는 keyword 전수 동기화를 건너뜁니다.")
            return

        result = await self.db.execute(select(Keyword.id, Keyword.tmdb_id, Keyword.name))
        rows = result.all()
        db_ids = {row.tmdb_id for row in rows}
        db_rows_by_tmdb_id = {row.tmdb_id: row for row in rows}
        db_name_lookup = build_normalized_lookup(rows, "name")
        dump_ids = set()
        new_keywords = []

        logger.info("덤프 파일 스트리밍 대조 시작...")
        for item in self.dump_fetcher.get_dump_iterator(file_path):
            tmdb_id = item["id"]
            keyword_name = item["name"]
            normalized_name = normalize_compare_text(keyword_name)
            dump_ids.add(tmdb_id)

            if tmdb_id in db_ids:
                existing_row = db_rows_by_tmdb_id.get(tmdb_id)
                if existing_row and normalize_compare_text(existing_row.name) != normalized_name:
                    await self.db.execute(
                        update(Keyword)
                        .where(Keyword.id == existing_row.id)
                        .values(name=keyword_name)
                    )
                    db_rows_by_tmdb_id[tmdb_id] = SimpleNamespace(
                        id=existing_row.id,
                        tmdb_id=tmdb_id,
                        name=keyword_name,
                    )
                    db_name_lookup[normalized_name] = db_rows_by_tmdb_id[tmdb_id]
                continue

            matched_row = db_name_lookup.get(normalized_name)
            if matched_row:
                await self.db.execute(
                    update(Keyword)
                    .where(Keyword.id == matched_row.id)
                    .values(tmdb_id=tmdb_id)
                )
                db_ids.discard(matched_row.tmdb_id)
                db_ids.add(tmdb_id)
                db_rows_by_tmdb_id.pop(matched_row.tmdb_id, None)
                db_rows_by_tmdb_id[tmdb_id] = SimpleNamespace(
                    id=matched_row.id,
                    tmdb_id=tmdb_id,
                    name=matched_row.name,
                )
                continue

            new_keywords.append({"tmdb_id": tmdb_id, "name": keyword_name})
            db_ids.add(tmdb_id)
            db_name_lookup[normalized_name] = SimpleNamespace(id=None, tmdb_id=tmdb_id, name=keyword_name)

        if new_keywords:
            chunk_size = 5000
            for index in range(0, len(new_keywords), chunk_size):
                chunk = new_keywords[index : index + chunk_size]
                stmt = insert(Keyword).values(chunk).on_conflict_do_nothing()
                await self.db.execute(stmt)
            logger.info("%s개의 신규 키워드 추가 완료.", len(new_keywords))

        delete_ids = db_ids - dump_ids
        if delete_ids:
            await self.db.execute(delete(Keyword).where(Keyword.tmdb_id.in_(list(delete_ids))))
            logger.info("%s개의 삭제된 키워드 정리 완료.", len(delete_ids))

        logger.info("키워드 동기화 완료.")
