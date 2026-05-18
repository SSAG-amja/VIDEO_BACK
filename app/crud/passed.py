from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import interaction as interaction_crud
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.schemas import passed as passed_schema


# 2026.05.13 박현식
# 관심없음 목록 응답에 사용할 영화 요약 DTO를 만든다.
def _passed_movie_summary(movie: movie_model.Movie) -> passed_schema.PassedMovieSummary:
    return passed_schema.PassedMovieSummary(
        movie_id=movie.tmdb_id,
        movie_title=movie.title_ko or movie.title or movie.original_title,
        poster_path=movie.poster_path,
    )


# 2026.05.13 박현식
# user_interactions에서 관심없음 상태인 영화 목록을 조회한다.
def get_passed_movies(db: Session, user_id: int, limit: int) -> passed_schema.PassedMovieListResponse:
    movies = db.execute(
        select(movie_model.Movie)
        .join(mapping_model.UserInteraction, mapping_model.UserInteraction.movie_id == movie_model.Movie.id)
        .where(
            mapping_model.UserInteraction.user_id == user_id,
            mapping_model.UserInteraction.is_passed.is_(True),
        )
        .limit(limit)
    ).scalars().all()

    return passed_schema.PassedMovieListResponse(
        total=len(movies),
        data=[_passed_movie_summary(movie) for movie in movies],
    )


# 2026.05.13 박현식
# 현재 사용자의 모든 관심없음 상태를 false로 변경한다.
def clear_passed_movies(db: Session, user_id: int) -> int:
    interactions = db.execute(
        select(mapping_model.UserInteraction).where(
            mapping_model.UserInteraction.user_id == user_id,
            mapping_model.UserInteraction.is_passed.is_(True),
        )
    ).scalars().all()

    for interaction in interactions:
        interaction.is_passed = False
    db.commit()
    return len(interactions)


# 2026.05.13 박현식
# 특정 영화의 관심없음 상태를 false로 변경하고 영화 요약을 반환한다.
def delete_passed_movie(db: Session, user_id: int, movie_id: int) -> passed_schema.PassedMovieSummary:
    movie = interaction_crud.get_movie_by_tmdb_id(db, movie_id)
    interaction = db.get(mapping_model.UserInteraction, {"user_id": user_id, "movie_id": movie.id})
    if interaction:
        interaction.is_passed = False
        db.commit()
    return _passed_movie_summary(movie)
