from sqlalchemy.orm import Session

from app.crud.recsys.ontology_recommendations import add_feed_event
from app.models.ontology_recommendations import RecommendationFeedEvent


def record_feed_event(db: Session, event: RecommendationFeedEvent) -> RecommendationFeedEvent:
    return add_feed_event(db, event)
