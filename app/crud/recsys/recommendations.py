from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.movie import Movie
from app.models.recommendations import Recommendation


# 2026.06.04 김호영
# 사용자별 사전 계산 추천 후보를 rank 순서로 조회한다.
def load_precomputed_candidates(db: Session, user_id: int, limit: int) -> list[tuple[Recommendation, Movie]]:
    stmt = (
        select(Recommendation, Movie)
        .join(Movie, Movie.id == Recommendation.movie_id)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.rank)
        .limit(limit)
    )
    return list(db.execute(stmt).all())


# 2026.06.04 김호영
# 특정 사용자의 기존 추천 후보를 새 추천 후보 목록으로 교체한다.
def replace_user_recommendation_rows(
    db: Session,
    user_id: int,
    recommendation_rows: list[Recommendation],
) -> None:
    db.query(Recommendation).filter(Recommendation.user_id == user_id).delete()
    db.add_all(recommendation_rows)
