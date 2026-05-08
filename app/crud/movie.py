from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.crud import user as user_crud
from app.models import movie as movie_model
from app.models import mapping as mapping_model
from app.schemas import movie as movie_schema


# 20260508 김광원
# 사용자 선호 장르 기반 영화 추천
def get_recommended_movies(db: Session, user_id: int, skip: int = 0, limit: int = 200) -> list[movie_model.Movie]:
    user = user_crud.get_user_with_preferences(db, user_id)
    if not user:
        return []

    genre_ids = [genre.id for genre in user.genres]
    
    stmt = (
        select(movie_model.Movie.id, movie_model.Movie.title_ko, movie_model.Movie.poster_path)
        .join(mapping_model.movie_genres)
        .where(
            mapping_model.movie_genres.c.genre_id.in_(genre_ids),
            movie_model.Movie.poster_path.is_not(None)
        )
        .distinct(movie_model.Movie.popularity)
        .order_by(desc(movie_model.Movie.popularity))
        .offset(skip)
        .limit(limit)
    )
    return  db.execute(stmt).mappings().all()