from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ontology_recommendations import OntologyRecommendation, RecommendationFeedEvent


def add_recommendation_snapshots(db: Session, rows: list[OntologyRecommendation]) -> None:
    db.add_all(rows)
    db.flush()


def list_request_recommendations(
    db: Session,
    *,
    request_id: str,
    candidate_stage: str = "final_response",
    limit: int = 50,
) -> list[OntologyRecommendation]:
    stmt = (
        select(OntologyRecommendation)
        .where(
            OntologyRecommendation.request_id == request_id,
            OntologyRecommendation.candidate_stage == candidate_stage,
        )
        .order_by(OntologyRecommendation.rank)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def add_feed_event(db: Session, event: RecommendationFeedEvent) -> RecommendationFeedEvent:
    db.add(event)
    db.flush()
    return event


def list_session_events(
    db: Session,
    *,
    feed_session_key: str,
    limit: int = 200,
) -> list[RecommendationFeedEvent]:
    stmt = (
        select(RecommendationFeedEvent)
        .where(RecommendationFeedEvent.feed_session_key == feed_session_key)
        .order_by(RecommendationFeedEvent.created_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
