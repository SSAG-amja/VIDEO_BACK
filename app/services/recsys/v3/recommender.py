from sqlalchemy.orm import Session

from app.schemas.recsys import RecommendationMode, RecommendationResponse
from app.services.recsys.v3.errors import V3NotReadyError


def get_recommendations(
    db: Session,
    *,
    user_id: int,
    mode: RecommendationMode,
    limit: int,
    offset: int = 0,
    shuffle_seed: str | None = None,
) -> RecommendationResponse:
    del db, user_id, mode, limit, offset, shuffle_seed
    raise V3NotReadyError("V3 serving pipeline is not implemented yet")


def refresh_cold_start(db: Session, *, user_id: int) -> None:
    del db, user_id
    raise V3NotReadyError("V3 cold-start pipeline is not implemented yet")
