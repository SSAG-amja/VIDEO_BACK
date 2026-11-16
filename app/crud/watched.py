from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.crud import interaction as interaction_crud
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.schemas import watched as watched_schema


# 2026.05.13 박현식
# 본 영화 목록 응답에 사용할 영화 요약 DTO를 만든다.
def _watched_movie_summary(
    movie: movie_model.Movie,
    watched_at: datetime | None = None,
) -> watched_schema.WatchedMovieSummary:
    return watched_schema.WatchedMovieSummary(
        movie_id=movie.tmdb_id,
        movie_title=movie.title_ko or movie.title or movie.original_title,
        poster_path=movie.poster_path,
        watched_at=watched_at,
    )


# 2026.05.13 박현식
# user_interactions에서 본 영화 상태인 영화 목록을 최신 시청순으로 조회한다.
def get_watched_movies(db: Session, user_id: int, limit: int) -> watched_schema.WatchedMovieListResponse:
    rows = db.execute(
        select(movie_model.Movie, mapping_model.UserInteraction.watched_at)
        .join(mapping_model.UserInteraction, mapping_model.UserInteraction.movie_id == movie_model.Movie.id)
        .where(
            mapping_model.UserInteraction.user_id == user_id,
            mapping_model.UserInteraction.is_watched.is_(True),
        )
        .order_by(
            desc(mapping_model.UserInteraction.watched_at).nulls_last(),
            desc(mapping_model.UserInteraction.movie_id),
        )
        .limit(limit)
    ).all()

    return watched_schema.WatchedMovieListResponse(
        total=len(rows),
        data=[_watched_movie_summary(movie, watched_at=watched_at) for movie, watched_at in rows],
    )


# 2026.05.13 박현식
# 현재 사용자의 모든 본 영화 상태를 false로 변경한다.
def clear_watched_movies(db: Session, user_id: int) -> int:
    interactions = db.execute(
        select(mapping_model.UserInteraction).where(
            mapping_model.UserInteraction.user_id == user_id,
            mapping_model.UserInteraction.is_watched.is_(True),
        )
    ).scalars().all()

    for interaction in interactions:
        interaction.is_watched = False
        interaction.watched_at = None
    db.commit()
    return len(interactions)


# 2026.05.13 박현식
# 특정 영화의 본 영화 상태를 false로 변경하고 영화 요약을 반환한다.
def delete_watched_movie(db: Session, user_id: int, movie_id: int) -> watched_schema.WatchedMovieSummary:
    movie = interaction_crud.get_movie_by_tmdb_id(db, movie_id)
    interaction = db.get(mapping_model.UserInteraction, {"user_id": user_id, "movie_id": movie.id})
    if interaction:
        interaction.is_watched = False
        interaction.watched_at = None
        db.commit()
    return _watched_movie_summary(movie)
