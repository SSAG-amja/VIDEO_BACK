from sqlalchemy.orm import Session
from sqlalchemy import select, desc, or_, case

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
        select(movie_model.Movie.tmdb_id, movie_model.Movie.title_ko, movie_model.Movie.poster_path)
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

def search_movies(
        db: Session,
        title: str | None = None,
        tag: str | None = None,
        genres: str | None = None,
        skip: int = 0,
        limit: int = 200
) -> list[movie_model.Movie]:
    if title:
        # 1: 완전 일치, 2: 시작 부분 일치, 3: 단순 포함
        relevance_expr = case(
            (movie_model.Movie.title_ko.ilike(title), 1),
            (movie_model.Movie.title_ko.ilike(f"{title}%", escape="\\"), 2),
            else_=3
        ).label("relevance")
    else:
        # 검색어가 없으면 전부 동일한 가중치(3) 부여
        relevance_expr = case((False, 1), else_=3).label("relevance")


    stmt = select(
        movie_model.Movie.tmdb_id,
        movie_model.Movie.title_ko,
        movie_model.Movie.poster_path,
        movie_model.Movie.popularity,
        relevance_expr
    )

    if title:
        search_kw = f"%{title}%"
        stmt = stmt.where(
            or_(
                movie_model.Movie.title_ko.ilike(search_kw),
            )
        )

    if genres:
        genre_ids = [int(g.strip()) for g in genres.split(",") if g.strip().isdigit()]
        if genre_ids:
            stmt = stmt.join(mapping_model.movie_genres).where(mapping_model.movie_genres.c.genre_id.in_(genre_ids))

    """
    태그 기능은 추후 구현 예정
    if tag:
    """   
    stmt = (
        stmt.distinct()
        .order_by(
            "relevance",
            desc(movie_model.Movie.popularity)
        )
        .offset(skip)
        .limit(limit)
    )

    return db.execute(stmt).mappings().all()