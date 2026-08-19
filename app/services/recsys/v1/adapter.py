from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import get_redis
from app.schemas.recsys import ColdStartRequest, RecommendationResponse
from app.services.recsys.contracts import RecommendationQuery
from app.services.recsys.v1.dynamic_retriever import build_cold_start_pool
from app.services.recsys.v1.recommendation import RecommendationOptions
from app.services.recsys.v1.recommendation import get_recommendations as get_v1_recommendations


class V1RecommendationAdapter:
    name = "v1"
    max_page_size = 200

    def get_recommendations(self, db: Session, query: RecommendationQuery) -> RecommendationResponse:
        return get_v1_recommendations(
            db,
            get_redis(),
            settings,
            RecommendationOptions(
                user_id=query.user_id,
                mode=query.mode,
                limit=min(query.limit, self.max_page_size),
                offset=query.offset,
                shuffle_seed=query.shuffle_seed,
            ),
        )

    def refresh_cold_start(self, db: Session, user_id: int) -> None:
        build_cold_start_pool(db, ColdStartRequest(user_id=user_id))
