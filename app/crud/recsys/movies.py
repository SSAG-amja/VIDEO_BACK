from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.mapping import MovieOtt, movie_genres
from app.models.movie import Movie


# 2026.06.04 김호영
# 동적 추천 후보 조회 시 이미 소비되었거나 추천된 영화 id를 제외한다.
def find_contextual_movies(
    db: Session,
    *,
    genre_ids: list[int] | None = None,
    ott_ids: list[int] | None = None,
    limit: int = 100,
    excluded_movie_ids: set[int] | None = None,
) -> list[Movie]:
    stmt = select(Movie).distinct()

    if genre_ids:
        stmt = stmt.join(movie_genres, movie_genres.c.movie_id == Movie.id).where(
            movie_genres.c.genre_id.in_(genre_ids)
        )
    if ott_ids:
        stmt = (
            stmt.join(MovieOtt, MovieOtt.movie_id == Movie.id)
            .where(MovieOtt.is_streaming.is_(True))
            .where(MovieOtt.ott_id.in_(ott_ids))
        )

    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))

    stmt = stmt.where(Movie.adult.is_(False)).order_by(desc(Movie.popularity)).limit(limit)
    return list(db.scalars(stmt).all())


# 2026.06.04 김호영
# 선호 장르 기반 인기 영화 후보를 조회한다.
def find_genre_popular_movies(
    db: Session,
    *,
    genre_ids: list[int],
    excluded_movie_ids: list[int],
    limit: int,
) -> list[Movie]:
    if not genre_ids or limit <= 0:
        return []

    stmt = (
        select(Movie)
        .distinct()
        .join(movie_genres, movie_genres.c.movie_id == Movie.id)
        .where(movie_genres.c.genre_id.in_(genre_ids))
        .where(Movie.adult.is_(False))
        .order_by(desc(Movie.popularity), desc(Movie.vote_average))
        .limit(limit)
    )
    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))
    return list(db.scalars(stmt))


# 2026.06.04 김호영
# fallback/explore용 전체 인기 영화 후보를 조회한다.
def find_popular_movies(
    db: Session,
    *,
    excluded_movie_ids: set[int],
    limit: int,
) -> list[Movie]:
    if limit <= 0:
        return []

    stmt = select(Movie).where(Movie.adult.is_(False)).order_by(desc(Movie.popularity), desc(Movie.vote_average))
    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))
    return list(db.scalars(stmt.limit(limit)))


# 2026.06.04 김호영
# 추천 후보 검증을 위해 실제 존재하는 영화 id 집합을 조회한다.
def load_existing_movie_ids(db: Session, movie_ids: list[int]) -> set[int]:
    if not movie_ids:
        return set()
    return set(db.scalars(select(Movie.id).where(Movie.id.in_(movie_ids))).all())


# 2026.06.04 김호영
# 사용자가 구독 중인 OTT에서 스트리밍 가능한 영화 id 집합을 조회한다.
def load_streaming_movie_ids(db: Session, ott_ids: list[int], movie_ids: list[int]) -> set[int]:
    if not ott_ids or not movie_ids:
        return set()

    stmt = (
        select(MovieOtt.movie_id)
        .where(MovieOtt.ott_id.in_(ott_ids))
        .where(MovieOtt.movie_id.in_(movie_ids))
        .where(MovieOtt.is_streaming.is_(True))
    )
    return set(db.scalars(stmt))


# 2026.06.04 김호영
# 특정 feature와 일치하는 영화 후보와 match count를 조회한다.
def iter_matching_movies(
    db: Session,
    mapping_source,
    movie_column,
    match_column,
    match_ids: list[int],
    excluded_movie_ids: set[int],
    limit: int,
):
    if not match_ids or limit <= 0:
        return []

    match_count = func.count(match_column).label("match_count")
    stmt = (
        select(Movie, match_count)
        .join(mapping_source, movie_column == Movie.id)
        .where(match_column.in_(match_ids))
        .where(Movie.adult.is_(False))
        .group_by(Movie.id)
        .order_by(desc(match_count), desc(Movie.popularity), desc(Movie.vote_average))
        .limit(limit)
    )
    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))
    return list(db.execute(stmt))


# 2026.06.04 김호영
# 추천 후보 영화별 스트리밍 OTT id 집합을 조회한다.
def load_movie_ott_map(db: Session, movie_ids: list[int]) -> dict[int, set[int]]:
    if not movie_ids:
        return {}

    rows = db.execute(
        select(MovieOtt.movie_id, MovieOtt.ott_id)
        .where(MovieOtt.movie_id.in_(movie_ids))
        .where(MovieOtt.is_streaming.is_(True))
    )

    ott_map: dict[int, set[int]] = {}
    for movie_id, ott_id in rows:
        ott_map.setdefault(movie_id, set()).add(ott_id)
    return ott_map
