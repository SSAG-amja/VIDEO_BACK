from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.mapping import UserInteraction


# 2026.06.04 김호영
# pass/watch 처리된 영화를 추천 후보에서 제외하기 위해 사용자 행동 테이블에서 조회한다.
def load_excluded_interaction_movie_ids(db: Session, user_id: int) -> set[int]:
    stmt = select(UserInteraction.movie_id).where(
        UserInteraction.user_id == user_id,
        or_(
            UserInteraction.is_passed.is_(True),
            UserInteraction.is_watched.is_(True),
        ),
    )
    return set(db.scalars(stmt).all())
