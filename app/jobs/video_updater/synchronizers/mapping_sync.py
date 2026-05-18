import logging

from sqlalchemy import delete, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from app.jobs.video_updater.utils.diff_calculator import DiffCalculator
from app.models import mapping as mapping_model

logger = logging.getLogger("MAPPING_SYNC")


class MappingSynchronizer:
    def __init__(self, db_session):
        self.db = db_session

    # 26.05.17 김광원
    # 영화 청크 단위의 매핑 diff를 계산해 BACK 매핑 테이블에 반영한다.
    async def sync_mappings(self, internal_movie_ids: list, parsed_api_mappings: dict):
        if not internal_movie_ids:
            return

        db_genres = await self._load_table_pairs(mapping_model.movie_genres, "genre_id", internal_movie_ids)
        db_keywords = await self._load_table_pairs(mapping_model.movie_keywords, "keyword_id", internal_movie_ids)
        db_directors = await self._load_table_pairs(mapping_model.movie_directors, "director_id", internal_movie_ids)
        db_actor_map = await self._load_actor_map(internal_movie_ids)
        db_otts = await self._load_ott_set(internal_movie_ids)
        api_actor_map = {(movie_id, actor_id): cast_name for movie_id, actor_id, cast_name in parsed_api_mappings["actors"]}

        add_g, del_g = DiffCalculator.get_delta(db_genres, parsed_api_mappings["genres"])
        add_k, del_k = DiffCalculator.get_delta(db_keywords, parsed_api_mappings["keywords"])
        add_d, del_d = DiffCalculator.get_delta(db_directors, parsed_api_mappings["directors"])
        add_o, del_o = DiffCalculator.get_delta(db_otts, parsed_api_mappings["otts"])
        add_a, del_a = self._get_actor_delta(db_actor_map, api_actor_map)

        if del_g:
            await self._execute_delete(mapping_model.movie_genres, "genre_id", del_g)
        if del_k:
            await self._execute_delete(mapping_model.movie_keywords, "keyword_id", del_k)
        if del_d:
            await self._execute_delete(mapping_model.movie_directors, "director_id", del_d)
        if del_a:
            await self._execute_delete(mapping_model.MovieActor, "actor_id", del_a)
        if del_o:
            await self._execute_delete(mapping_model.MovieOtt, "ott_id", del_o)

        await self._insert_table_pairs(mapping_model.movie_genres, "genre_id", add_g)
        await self._insert_table_pairs(mapping_model.movie_keywords, "keyword_id", add_k)
        await self._insert_table_pairs(mapping_model.movie_directors, "director_id", add_d)
        await self._insert_actors(add_a)
        await self._insert_otts(add_o)

    # 26.05.17 김광원
    # BACK Table 객체 기반 매핑 데이터를 조회한다.
    async def _load_table_pairs(self, table, target_column: str, movie_ids: list) -> set:
        result = await self.db.execute(
            select(table.c.movie_id, getattr(table.c, target_column)).where(table.c.movie_id.in_(movie_ids))
        )
        return {(row.movie_id, getattr(row, target_column)) for row in result.all()}

    # 26.05.17 김광원
    # 배우 매핑은 cast_name 변경까지 비교하기 위해 dict로 조회한다.
    async def _load_actor_map(self, movie_ids: list) -> dict:
        result = await self.db.execute(
            select(
                mapping_model.MovieActor.movie_id,
                mapping_model.MovieActor.actor_id,
                mapping_model.MovieActor.cast_name,
            ).where(mapping_model.MovieActor.movie_id.in_(movie_ids))
        )
        return {(row.movie_id, row.actor_id): row.cast_name for row in result.all()}

    # 26.05.17 김광원
    # OTT 매핑은 서비스 타입 플래그까지 포함해 조회한다.
    async def _load_ott_set(self, movie_ids: list) -> set:
        result = await self.db.execute(
            select(
                mapping_model.MovieOtt.movie_id,
                mapping_model.MovieOtt.ott_id,
                mapping_model.MovieOtt.is_streaming,
                mapping_model.MovieOtt.is_rent,
                mapping_model.MovieOtt.is_buy,
            ).where(mapping_model.MovieOtt.movie_id.in_(movie_ids))
        )
        return {
            (row.movie_id, row.ott_id, row.is_streaming, row.is_rent, row.is_buy)
            for row in result.all()
        }

    # 26.05.17 김광원
    # 배우 매핑의 추가/삭제 대상을 PK와 cast_name 기준으로 계산한다.
    def _get_actor_delta(self, db_actor_map: dict, api_actor_map: dict) -> tuple[set, set]:
        add_actors = set()
        delete_actors = set()

        for actor_key, cast_name in api_actor_map.items():
            if actor_key not in db_actor_map or db_actor_map[actor_key] != cast_name:
                add_actors.add((actor_key[0], actor_key[1], cast_name))

        for actor_key, cast_name in db_actor_map.items():
            if actor_key not in api_actor_map or api_actor_map[actor_key] != cast_name:
                delete_actors.add((actor_key[0], actor_key[1], cast_name))

        return add_actors, delete_actors

    # 26.05.17 김광원
    # 복합키 매핑 row를 일괄 삭제한다.
    async def _execute_delete(self, target, target_id_column: str, delete_list: list):
        movie_column = target.c.movie_id if hasattr(target, "c") else target.movie_id
        target_column = getattr(target.c, target_id_column) if hasattr(target, "c") else getattr(target, target_id_column)
        target_tuples = [(movie_id, target_id) for movie_id, target_id, *_ in delete_list]
        stmt = delete(target).where(tuple_(movie_column, target_column).in_(target_tuples))
        await self.db.execute(stmt)

    # 26.05.17 김광원
    # BACK Table 객체 기반 매핑 row를 일괄 추가한다.
    async def _insert_table_pairs(self, table, target_column: str, add_list: list):
        if not add_list:
            return

        await self.db.execute(
            insert(table)
            .values([{"movie_id": movie_id, target_column: target_id} for movie_id, target_id in add_list])
            .on_conflict_do_nothing()
        )

    # 26.05.17 김광원
    # 배우 매핑을 추가하고 기존 row는 cast_name만 갱신한다.
    async def _insert_actors(self, add_actors: set):
        if not add_actors:
            return

        actor_insert_stmt = insert(mapping_model.MovieActor).values(
            [
                {"movie_id": movie_id, "actor_id": actor_id, "cast_name": cast_name}
                for movie_id, actor_id, cast_name in add_actors
            ]
        )
        await self.db.execute(
            actor_insert_stmt.on_conflict_do_update(
                index_elements=["movie_id", "actor_id"],
                set_={"cast_name": actor_insert_stmt.excluded.cast_name},
            )
        )

    # 26.05.17 김광원
    # OTT 매핑을 추가하고 기존 row는 서비스 타입 플래그만 갱신한다.
    async def _insert_otts(self, add_otts: set):
        if not add_otts:
            return

        ott_insert_stmt = insert(mapping_model.MovieOtt).values(
            [
                {
                    "movie_id": movie_id,
                    "ott_id": ott_id,
                    "is_streaming": is_streaming,
                    "is_rent": is_rent,
                    "is_buy": is_buy,
                }
                for movie_id, ott_id, is_streaming, is_rent, is_buy in add_otts
            ]
        )
        await self.db.execute(
            ott_insert_stmt.on_conflict_do_update(
                index_elements=["movie_id", "ott_id"],
                set_={
                    "is_streaming": ott_insert_stmt.excluded.is_streaming,
                    "is_rent": ott_insert_stmt.excluded.is_rent,
                    "is_buy": ott_insert_stmt.excluded.is_buy,
                },
            )
        )
