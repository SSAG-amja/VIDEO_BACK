from sqlalchemy.orm import Session

from app.schemas.recsys import RecommendationResponse
from app.services.recsys.contracts import RecommendationQuery
from app.services.recsys.v4.config import MAX_PAGE_SIZE
from app.services.recsys.v4.recommender import get_recommendations as get_v4_recommendations


class V4RecommendationAdapter:
    name = "v4"
    max_page_size = MAX_PAGE_SIZE

    def get_recommendations(
        self,
        db: Session,
        query: RecommendationQuery,
    ) -> RecommendationResponse:
        return get_v4_recommendations(
            db,
            user_id=query.user_id,
            mode=query.mode,
            limit=min(query.limit, self.max_page_size),
            offset=query.offset,
            shuffle_seed=query.shuffle_seed,
        )

    def refresh_cold_start(self, db: Session, user_id: int) -> None:
        # V4 refreshes onboarding-derived candidates on the next request.
        return None
