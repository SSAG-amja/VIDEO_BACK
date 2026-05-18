import logging

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from app.jobs.video_updater.fetchers.api_fetcher import TMDBApiFetcher
from app.jobs.video_updater.utils.compare_utils import build_normalized_lookup, normalize_compare_text
from app.models.genre import Genre
from app.models.ott import Ott

logger = logging.getLogger("META_SYNC")


class MetaSynchronizer:
    def __init__(self, db_session):
        self.db = db_session
        self.fetcher = TMDBApiFetcher()

    # 26.05.17 김광원
    # TMDB 장르 목록을 BACK genres 테이블 기준으로 동기화한다.
    async def sync_genres(self, session):
        logger.info("장르 동기화 시작...")

        data_us = await self.fetcher.fetch_genres(session, language="en-US")
        data_ko = await self.fetcher.fetch_genres(session, language="ko-KR")
        if not data_us:
            logger.error("장르 데이터를 가져오는데 실패했습니다.")
            return

        genre_ko_map = {item["id"]: item.get("name") for item in data_ko}
        result = await self.db.execute(select(Genre.id, Genre.tmdb_id, Genre.name, Genre.name_ko))
        rows = result.all()
        existing_ids = {row.tmdb_id for row in rows}
        existing_names = build_normalized_lookup(rows, "name")
        new_genres = []

        for item in data_us:
            genre_id = item["id"]
            localized_name = genre_ko_map.get(genre_id) or item["name"]

            if genre_id in existing_ids:
                await self.db.execute(
                    update(Genre)
                    .where(Genre.tmdb_id == genre_id)
                    .values(name=item["name"], name_ko=localized_name)
                )
                continue

            matched_row = existing_names.get(normalize_compare_text(item["name"]))
            if matched_row:
                await self.db.execute(
                    update(Genre)
                    .where(Genre.id == matched_row.id)
                    .values(tmdb_id=genre_id, name=item["name"], name_ko=localized_name)
                )
                existing_ids.discard(matched_row.tmdb_id)
                existing_ids.add(genre_id)
                continue

            new_genres.append(
                {
                    "tmdb_id": genre_id,
                    "name": item["name"],
                    "name_ko": localized_name,
                }
            )

        if new_genres:
            stmt = insert(Genre).values(new_genres).on_conflict_do_nothing(index_elements=["tmdb_id"])
            await self.db.execute(stmt)
            logger.info("%s개의 새로운 장르가 추가되었습니다.", len(new_genres))
            return

        logger.info("업데이트할 새로운 장르가 없습니다.")

    # 26.05.17 김광원
    # TMDB 한국 지역 OTT 목록을 BACK otts 테이블 기준으로 동기화한다.
    async def sync_otts(self, session):
        logger.info("OTT 목록 동기화 시작...")

        data_us = await self.fetcher.fetch_otts(session, language="en-US")
        data_ko = await self.fetcher.fetch_otts(session, language="ko-KR")
        if not data_us:
            logger.error("OTT 데이터를 가져오는데 실패했습니다.")
            return

        ott_ko_map = {item["provider_id"]: item.get("provider_name") for item in data_ko}
        result = await self.db.execute(select(Ott.id, Ott.tmdb_id, Ott.name, Ott.name_ko))
        rows = result.all()
        existing_ids = {row.tmdb_id for row in rows}
        existing_names = build_normalized_lookup(rows, "name")
        new_otts = []

        for item in data_us:
            provider_id = item["provider_id"]
            localized_name = ott_ko_map.get(provider_id) or item["provider_name"]

            if provider_id in existing_ids:
                await self.db.execute(
                    update(Ott)
                    .where(Ott.tmdb_id == provider_id)
                    .values(name=item["provider_name"], name_ko=localized_name)
                )
                continue

            matched_row = existing_names.get(normalize_compare_text(item["provider_name"]))
            if matched_row:
                await self.db.execute(
                    update(Ott)
                    .where(Ott.id == matched_row.id)
                    .values(tmdb_id=provider_id, name=item["provider_name"], name_ko=localized_name)
                )
                existing_ids.discard(matched_row.tmdb_id)
                existing_ids.add(provider_id)
                continue

            new_otts.append(
                {
                    "tmdb_id": provider_id,
                    "name": item["provider_name"],
                    "name_ko": localized_name,
                }
            )

        if new_otts:
            stmt = insert(Ott).values(new_otts).on_conflict_do_nothing(index_elements=["tmdb_id"])
            await self.db.execute(stmt)
            logger.info("%s개의 새로운 OTT가 추가되었습니다.", len(new_otts))
            return

        logger.info("업데이트할 새로운 OTT가 없습니다.")
