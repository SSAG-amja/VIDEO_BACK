from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mapping import (
    MovieActor,
    movie_directors,
    movie_genres,
    movie_keywords,
    user_favorite_movies,
    user_genres,
    user_otts,
)


# 2026.06.04 김호영
# 온보딩에서 선택한 선호 영화 id를 조회한다.
def load_user_favorite_movie_ids(db: Session, user_id: int) -> list[int]:
    return list(
        db.scalars(
            select(user_favorite_movies.c.movie_id).where(user_favorite_movies.c.user_id == user_id)
        )
    )


# 2026.06.04 김호영
# 온보딩에서 선택한 선호 장르 id를 조회한다.
def load_user_genre_ids(db: Session, user_id: int) -> list[int]:
    return list(db.scalars(select(user_genres.c.genre_id).where(user_genres.c.user_id == user_id)))


# 2026.06.04 김호영
# 온보딩에서 선택한 구독 OTT id를 조회한다.
def load_user_ott_ids(db: Session, user_id: int) -> list[int]:
    return list(db.scalars(select(user_otts.c.ott_id).where(user_otts.c.user_id == user_id)))


# 2026.06.04 김호영
# 선호 영화들의 장르 feature id를 조회한다.
def load_movie_genre_ids(db: Session, movie_ids: list[int]) -> list[int]:
    return _mapping_values(db, movie_genres.c.genre_id, movie_genres.c.movie_id, movie_ids)


# 2026.06.04 김호영
# 선호 영화들의 키워드 feature id를 조회한다.
def load_movie_keyword_ids(db: Session, movie_ids: list[int]) -> list[int]:
    return _mapping_values(db, movie_keywords.c.keyword_id, movie_keywords.c.movie_id, movie_ids)


# 2026.06.04 김호영
# 선호 영화들의 배우 feature id를 조회한다.
def load_movie_actor_ids(db: Session, movie_ids: list[int]) -> list[int]:
    return _mapping_values(db, MovieActor.actor_id, MovieActor.movie_id, movie_ids)


# 2026.06.04 김호영
# 선호 영화들의 감독 feature id를 조회한다.
def load_movie_director_ids(db: Session, movie_ids: list[int]) -> list[int]:
    return _mapping_values(db, movie_directors.c.director_id, movie_directors.c.movie_id, movie_ids)


def _mapping_values(db: Session, value_column, movie_column, movie_ids: list[int]) -> list[int]:
    if not movie_ids:
        return []
    return list(db.scalars(select(value_column).where(movie_column.in_(movie_ids)).distinct()))
