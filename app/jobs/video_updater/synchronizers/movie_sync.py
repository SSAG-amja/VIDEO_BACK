import asyncio
import logging
from datetime import date

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from app.jobs.video_updater.fetchers.api_fetcher import TMDBApiFetcher
from app.jobs.video_updater.fetchers.dump_fetcher import TMDBDumpFetcher
from app.jobs.video_updater.synchronizers.mapping_sync import MappingSynchronizer
from app.jobs.video_updater.utils.compare_utils import build_normalized_lookup, normalize_compare_text
from app.models.genre import Genre
from app.models.keyword import Keyword
from app.models.movie import Movie
from app.models.ott import Ott
from app.models.people import People

logger = logging.getLogger("MOVIE_SYNC")


# 26.05.17 김광원
# TMDB 응답에서 한국 개봉일을 우선순위 기준으로 추출한다.
def get_korean_release_date(movie_data):
    release_date_groups = movie_data.get("release_dates", {}).get("results", [])

    for country in release_date_groups:
        if country.get("iso_3166_1") != "KR":
            continue

        release_items = country.get("release_dates", [])
        for preferred_type in (3, 2):
            typed_dates = [
                info.get("release_date")
                for info in release_items
                if info.get("type") == preferred_type and info.get("release_date")
            ]
            if typed_dates:
                return min(typed_dates).split("T")[0]

        all_dates = [info.get("release_date") for info in release_items if info.get("release_date")]
        if all_dates:
            return min(all_dates).split("T")[0]

    return None


# 26.05.17 김광원
# 한국어 영화 상세 응답에서 DB에 반영할 현지화 필드를 추출한다.
def get_korean_movie_fields(movie_data):
    return {
        "title_ko": movie_data.get("title"),
        "status": movie_data.get("status"),
        "poster_path": movie_data.get("poster_path"),
        "backdrop_path": movie_data.get("backdrop_path"),
    }


# 26.05.17 김광원
# TMDB 날짜 문자열을 Date 컬럼에 넣을 수 있는 date 객체로 변환한다.
def parse_release_date(value):
    if not value:
        return None

    if isinstance(value, date):
        return value

    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# 26.05.17 김광원
# 동일 배우의 여러 역할명 중 대표 cast_name을 선택한다.
def choose_cast_name(current_value, candidate_value):
    current_text = (current_value or "").strip()
    candidate_text = (candidate_value or "").strip()

    if not current_text:
        return candidate_text
    if not candidate_text:
        return current_text

    return candidate_text if len(candidate_text) > len(current_text) else current_text


class MovieSynchronizer:
    def __init__(self, db_session):
        self.db = db_session
        self.api_fetcher = TMDBApiFetcher()
        self.dump_fetcher = TMDBDumpFetcher()
        self.genre_map = {}
        self.ott_map = {}
        self.keyword_map = {}
        self.keyword_name_lookup = {}
        self.person_map = {}

    # 26.05.17 김광원
    # 매핑 생성에 필요한 TMDB id와 내부 DB id 매핑을 메모리에 올린다.
    async def _build_memory_maps(self):
        logger.info("메타데이터 ID 메모리 맵 구축 중...")

        for model, target_map in (
            (Genre, self.genre_map),
            (Ott, self.ott_map),
            (People, self.person_map),
        ):
            result = await self.db.execute(select(model.tmdb_id, model.id))
            target_map.update({row.tmdb_id: row.id for row in result.all()})

        keyword_result = await self.db.execute(select(Keyword.id, Keyword.tmdb_id, Keyword.name))
        keyword_rows = keyword_result.all()
        self.keyword_map.update({row.tmdb_id: row.id for row in keyword_rows})
        self.keyword_name_lookup = build_normalized_lookup(keyword_rows, "name")

    # 26.05.17 김광원
    # dump와 change API를 사용해 movies 및 관련 매핑을 동기화한다.
    async def sync_movies(self, aio_session, date_str: str, start_date: str, end_date: str):
        logger.info("영화 메인 동기화 파이프라인 가동...")

        await self._build_memory_maps()
        movie_rows = await self._load_movie_rows()
        db_movies = {row.tmdb_id: row.original_title for row in movie_rows}
        db_movie_lookup = {
            (normalize_compare_text(row.original_title), normalize_compare_text(row.original_language)): row
            for row in movie_rows
        }
        db_movie_title_lookup = build_normalized_lookup(movie_rows, "original_title")

        new_ids, title_changed_ids, reconciled_ids = await self._sync_dump_movies(
            aio_session,
            date_str,
            db_movies,
            db_movie_lookup,
            db_movie_title_lookup,
        )

        changed_api_ids = await self._load_changed_movie_ids(aio_session, start_date, end_date)
        target_ids = list(new_ids | title_changed_ids | changed_api_ids | reconciled_ids)
        if not target_ids:
            logger.info("업데이트할 영화가 없습니다. 파이프라인 종료.")
            return

        logger.info("총 %s편의 영화 상세 업데이트를 시작합니다.", len(target_ids))
        await self._process_movie_chunk(aio_session, target_ids)

    # 26.05.17 김광원
    # 기존 영화 row 중 동기화 비교에 필요한 컬럼만 조회한다.
    async def _load_movie_rows(self):
        result = await self.db.execute(
            select(
                Movie.id,
                Movie.tmdb_id,
                Movie.original_title,
                Movie.original_language,
                Movie.release_date,
                Movie.title_ko,
                Movie.status,
                Movie.poster_path,
                Movie.backdrop_path,
            )
        )
        return result.all()

    # 26.05.17 김광원
    # movie dump로 신규/제목 변경/삭제/기존 row 보정 대상을 계산한다.
    async def _sync_dump_movies(
        self,
        aio_session,
        date_str: str,
        db_movies: dict,
        db_movie_lookup: dict,
        db_movie_title_lookup: dict,
    ) -> tuple[set, set, set]:
        logger.info("[Phase 2] 영화 덤프 대조 시작...")
        dump_file = await self.dump_fetcher.download_dump("movie_ids", date_str)
        new_ids = set()
        title_changed_ids = set()
        reconciled_ids = set()

        if not dump_file:
            logger.warning("movie dump 파일을 받지 못해 삭제 단계는 건너뜁니다.")
            return new_ids, title_changed_ids, reconciled_ids

        dump_ids = set()
        for item in self.dump_fetcher.get_dump_iterator(dump_file):
            tmdb_id = item["id"]
            original_title = item.get("original_title")
            original_language = item.get("original_language")
            dump_ids.add(tmdb_id)

            if tmdb_id in db_movies:
                if db_movies[tmdb_id] != original_title:
                    title_changed_ids.add(tmdb_id)
                continue

            matched_row = db_movie_lookup.get(
                (normalize_compare_text(original_title), normalize_compare_text(original_language))
            ) or db_movie_title_lookup.get(normalize_compare_text(original_title))

            if not matched_row:
                new_ids.add(tmdb_id)
                continue

            reconciled = await self._reconcile_movie_id(
                aio_session,
                tmdb_id,
                original_title,
                original_language,
                matched_row,
                db_movies,
                db_movie_lookup,
                db_movie_title_lookup,
            )
            if reconciled:
                reconciled_ids.add(tmdb_id)
            else:
                new_ids.add(tmdb_id)

        delete_ids = set(db_movies.keys()) - dump_ids
        if delete_ids:
            await self.db.execute(delete(Movie).where(Movie.tmdb_id.in_(list(delete_ids))))
            logger.info("%s편의 영화 삭제 완료 (매핑 테이블 연쇄 삭제됨).", len(delete_ids))

        return new_ids, title_changed_ids, reconciled_ids

    # 26.05.17 김광원
    # 기존 seed row와 TMDB row가 같은 영화인지 검증한 뒤 tmdb_id를 보정한다.
    async def _reconcile_movie_id(
        self,
        aio_session,
        tmdb_id: int,
        original_title: str,
        original_language: str,
        matched_row,
        db_movies: dict,
        db_movie_lookup: dict,
        db_movie_title_lookup: dict,
    ) -> bool:
        detail = await self.api_fetcher.fetch_movie_details(aio_session, tmdb_id)
        if not detail:
            return False

        if normalize_compare_text(detail.get("original_title")) != normalize_compare_text(matched_row.original_title):
            return False
        if normalize_compare_text(detail.get("original_language")) != normalize_compare_text(matched_row.original_language):
            return False
        if normalize_compare_text(get_korean_release_date(detail)) != normalize_compare_text(matched_row.release_date):
            return False

        await self.db.execute(update(Movie).where(Movie.id == matched_row.id).values(tmdb_id=tmdb_id))

        old_tmdb_id = matched_row.tmdb_id
        db_movies.pop(old_tmdb_id, None)
        db_movies[tmdb_id] = original_title
        db_movie_lookup.pop(
            (
                normalize_compare_text(matched_row.original_title),
                normalize_compare_text(matched_row.original_language),
            ),
            None,
        )
        db_movie_lookup[(normalize_compare_text(original_title), normalize_compare_text(original_language))] = matched_row
        db_movie_title_lookup.pop(normalize_compare_text(matched_row.original_title), None)
        db_movie_title_lookup[normalize_compare_text(original_title)] = matched_row
        return True

    # 26.05.17 김광원
    # movie changes API에서 변경된 영화 id를 전부 수집한다.
    async def _load_changed_movie_ids(self, aio_session, start_date: str, end_date: str) -> set:
        logger.info("[Phase 3] 영화 변경분(Change API) 수집 중...")
        changed_api_ids = set()
        page = 1

        while True:
            change_data = await self.api_fetcher.fetch_changes(
                aio_session,
                start_date,
                end_date,
                page=page,
                endpoint="/movie/changes",
            )

            if not change_data:
                break

            changed_api_ids.update(item["id"] for item in change_data.get("results", []))

            total_pages = change_data.get("total_pages", 1)
            if page >= total_pages:
                break

            page += 1

        return changed_api_ids

    # 26.05.17 김광원
    # 영화 상세 데이터를 청크 단위로 upsert하고 매핑 동기화를 실행한다.
    async def _process_movie_chunk(self, aio_session, target_ids: list):
        chunk_size = 1000
        total_chunks = (len(target_ids) - 1) // chunk_size + 1

        for index in range(0, len(target_ids), chunk_size):
            chunk_ids = target_ids[index : index + chunk_size]
            current_chunk = index // chunk_size + 1
            logger.info("[%s/%s] 영화 청크 처리 중... (%s건)", current_chunk, total_chunks, len(chunk_ids))

            us_tasks = [self.api_fetcher.fetch_movie_details(aio_session, movie_id) for movie_id in chunk_ids]
            us_results = await asyncio.gather(*us_tasks)
            valid_movies = [movie for movie in us_results if movie is not None]
            if not valid_movies:
                continue

            ko_map = await self._load_korean_movie_map(aio_session, valid_movies)
            movies_to_upsert, new_keywords_to_insert, new_people_to_insert = self._parse_movie_rows(valid_movies, ko_map)

            await self._upsert_new_keywords(new_keywords_to_insert)
            await self._upsert_new_people(new_people_to_insert)
            movie_id_map = await self._upsert_movies(movies_to_upsert)
            await self._sync_movie_mappings(valid_movies, movie_id_map)

        logger.info("모든 영화 상세 동기화가 완료되었습니다.")

    # 26.05.17 김광원
    # 한국어 영화 상세 응답을 영화 id 기준 dict로 만든다.
    async def _load_korean_movie_map(self, aio_session, valid_movies: list) -> dict:
        ko_tasks = [
            self.api_fetcher.fetch_movie_details(aio_session, movie["id"], language="ko-KR")
            for movie in valid_movies
        ]
        ko_results = await asyncio.gather(*ko_tasks)
        return {
            movie["id"]: movie
            for movie in ko_results
            if movie is not None and movie.get("id") is not None
        }

    # 26.05.17 김광원
    # TMDB 영화 상세 응답을 movies upsert row와 신규 메타 후보로 분리한다.
    def _parse_movie_rows(self, valid_movies: list, ko_map: dict) -> tuple[list, dict, dict]:
        movies_to_upsert = []
        new_keywords_to_insert = {}
        new_people_to_insert = {}

        for movie_data in valid_movies:
            localized_data = ko_map.get(movie_data["id"]) or movie_data
            ko_fields = get_korean_movie_fields(localized_data)
            release_date = get_korean_release_date(localized_data) or localized_data.get("release_date")

            movies_to_upsert.append(
                {
                    "tmdb_id": movie_data["id"],
                    "imdb_id": movie_data.get("imdb_id"),
                    "title": movie_data.get("title"),
                    "title_ko": ko_fields.get("title_ko"),
                    "original_title": movie_data.get("original_title"),
                    "original_language": movie_data.get("original_language"),
                    "overview": movie_data.get("overview"),
                    "popularity": movie_data.get("popularity", 0.0),
                    "vote_average": movie_data.get("vote_average", 0.0),
                    "vote_count": movie_data.get("vote_count", 0),
                    "release_date": parse_release_date(release_date),
                    "runtime": movie_data.get("runtime", 0),
                    "budget": movie_data.get("budget", 0),
                    "revenue": movie_data.get("revenue", 0),
                    "adult": movie_data.get("adult", False),
                    "status": ko_fields.get("status"),
                    "poster_path": ko_fields.get("poster_path"),
                    "backdrop_path": ko_fields.get("backdrop_path"),
                }
            )

            for keyword in movie_data.get("keywords", {}).get("keywords", []):
                if keyword["id"] not in self.keyword_map and keyword.get("name"):
                    new_keywords_to_insert[keyword["id"]] = keyword["name"]

            credits = movie_data.get("credits", {})
            for cast in credits.get("cast", []):
                if cast["id"] not in self.person_map:
                    new_people_to_insert[cast["id"]] = cast["name"]
            for crew in credits.get("crew", []):
                if crew["job"] == "Director" and crew["id"] not in self.person_map:
                    new_people_to_insert[crew["id"]] = crew["name"]

        return movies_to_upsert, new_keywords_to_insert, new_people_to_insert

    # 26.05.17 김광원
    # 영화 상세에서 새로 발견한 keyword를 삽입하고 메모리 맵을 갱신한다.
    async def _upsert_new_keywords(self, new_keywords_to_insert: dict):
        if not new_keywords_to_insert:
            return

        keyword_values = []
        for keyword_id, name in new_keywords_to_insert.items():
            normalized_name = normalize_compare_text(name)
            matched_row = self.keyword_name_lookup.get(normalized_name)
            if matched_row:
                self.keyword_map[keyword_id] = matched_row.id
                continue

            keyword_values.append({"tmdb_id": keyword_id, "name": name})

        if not keyword_values:
            return

        stmt = (
            insert(Keyword)
            .values(keyword_values)
            .on_conflict_do_nothing()
            .returning(Keyword.id, Keyword.tmdb_id, Keyword.name)
        )
        result = await self.db.execute(stmt)
        for row in result.all():
            self.keyword_map[row.tmdb_id] = row.id
            self.keyword_name_lookup[normalize_compare_text(row.name)] = row

        refreshed_keywords = await self.db.execute(
            select(Keyword.id, Keyword.tmdb_id, Keyword.name).where(
                Keyword.tmdb_id.in_([item["tmdb_id"] for item in keyword_values])
            )
        )
        for row in refreshed_keywords.all():
            self.keyword_map[row.tmdb_id] = row.id
            self.keyword_name_lookup[normalize_compare_text(row.name)] = row

    # 26.05.17 김광원
    # 영화 상세에서 새로 발견한 인물을 삽입하고 메모리 맵을 갱신한다.
    async def _upsert_new_people(self, new_people_to_insert: dict):
        if not new_people_to_insert:
            return

        people_values = [
            {"tmdb_id": person_id, "name": name, "name_ko": name}
            for person_id, name in new_people_to_insert.items()
        ]
        stmt = (
            insert(People)
            .values(people_values)
            .on_conflict_do_nothing(index_elements=["tmdb_id"])
            .returning(People.id, People.tmdb_id)
        )
        result = await self.db.execute(stmt)
        for row in result.all():
            self.person_map[row.tmdb_id] = row.id

        refreshed_people = await self.db.execute(
            select(People.id, People.tmdb_id).where(People.tmdb_id.in_(list(new_people_to_insert.keys())))
        )
        for row in refreshed_people.all():
            self.person_map[row.tmdb_id] = row.id

    # 26.05.17 김광원
    # movies 테이블에 영화 상세 row를 upsert하고 내부 movie id map을 반환한다.
    async def _upsert_movies(self, movies_to_upsert: list) -> dict:
        stmt = insert(Movie).values(movies_to_upsert)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tmdb_id"],
            set_={column.name: column for column in stmt.excluded if column.name not in ("id", "tmdb_id")},
        ).returning(Movie.id, Movie.tmdb_id)

        result = await self.db.execute(stmt)
        return {row.tmdb_id: row.id for row in result.all()}

    # 26.05.17 김광원
    # 영화 상세 응답을 BACK 매핑 테이블 구조에 맞게 변환해 동기화한다.
    async def _sync_movie_mappings(self, valid_movies: list, movie_id_map: dict):
        internal_movie_ids = list(movie_id_map.values())
        if not internal_movie_ids:
            return

        api_mappings = {
            "genres": set(),
            "otts": set(),
            "keywords": set(),
            "actors": {},
            "directors": set(),
        }

        for movie_data in valid_movies:
            internal_movie_id = movie_id_map.get(movie_data["id"])
            if not internal_movie_id:
                continue

            self._append_genre_mappings(movie_data, internal_movie_id, api_mappings)
            self._append_keyword_mappings(movie_data, internal_movie_id, api_mappings)
            self._append_ott_mappings(movie_data, internal_movie_id, api_mappings)
            self._append_people_mappings(movie_data, internal_movie_id, api_mappings)

        normalized_actor_mappings = {
            (movie_id, actor_id, cast_name)
            for (movie_id, actor_id), cast_name in api_mappings["actors"].items()
        }
        mapping_sync = MappingSynchronizer(self.db)
        await mapping_sync.sync_mappings(
            internal_movie_ids,
            {
                **api_mappings,
                "actors": normalized_actor_mappings,
            },
        )

    # 26.05.17 김광원
    # 영화 장르 매핑 후보를 추가한다.
    def _append_genre_mappings(self, movie_data: dict, internal_movie_id: int, api_mappings: dict):
        for genre in movie_data.get("genres", []):
            if genre["id"] in self.genre_map:
                api_mappings["genres"].add((internal_movie_id, self.genre_map[genre["id"]]))

    # 26.05.17 김광원
    # 영화 키워드 매핑 후보를 추가한다.
    def _append_keyword_mappings(self, movie_data: dict, internal_movie_id: int, api_mappings: dict):
        for keyword in movie_data.get("keywords", {}).get("keywords", []):
            if keyword["id"] in self.keyword_map:
                api_mappings["keywords"].add((internal_movie_id, self.keyword_map[keyword["id"]]))

    # 26.05.17 김광원
    # 한국 지역 OTT 매핑 후보를 서비스 타입 플래그와 함께 추가한다.
    def _append_ott_mappings(self, movie_data: dict, internal_movie_id: int, api_mappings: dict):
        kr_providers = movie_data.get("watch/providers", {}).get("results", {}).get("KR", {})
        ott_dict = {}
        for provider_type, flag in (("flatrate", "is_streaming"), ("rent", "is_rent"), ("buy", "is_buy")):
            for provider_data in kr_providers.get(provider_type, []):
                provider_id = provider_data["provider_id"]
                if provider_id not in self.ott_map:
                    continue
                if provider_id not in ott_dict:
                    ott_dict[provider_id] = {"is_streaming": False, "is_rent": False, "is_buy": False}
                ott_dict[provider_id][flag] = True

        for provider_id, flags in ott_dict.items():
            api_mappings["otts"].add(
                (
                    internal_movie_id,
                    self.ott_map[provider_id],
                    flags["is_streaming"],
                    flags["is_rent"],
                    flags["is_buy"],
                )
            )

    # 26.05.17 김광원
    # 영화 배우/감독 매핑 후보를 추가한다.
    def _append_people_mappings(self, movie_data: dict, internal_movie_id: int, api_mappings: dict):
        for cast in movie_data.get("credits", {}).get("cast", []):
            if cast["id"] in self.person_map:
                actor_key = (internal_movie_id, self.person_map[cast["id"]])
                api_mappings["actors"][actor_key] = choose_cast_name(
                    api_mappings["actors"].get(actor_key),
                    cast.get("character", "")[:100],
                )

        for crew in movie_data.get("credits", {}).get("crew", []):
            if crew["job"] == "Director" and crew["id"] in self.person_map:
                api_mappings["directors"].add((internal_movie_id, self.person_map[crew["id"]]))
