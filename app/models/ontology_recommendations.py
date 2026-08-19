from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func, text

from app.db.base import Base


class OntologyRecommendation(Base):
    __tablename__ = "ontology_recommendations"
    __table_args__ = (
        UniqueConstraint("request_id", "candidate_stage", "movie_id", name="uq_ontology_recs_request_stage_movie"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(64), ForeignKey("recommendation_runs.run_id", ondelete="CASCADE"), nullable=False)
    request_id = Column(String(64), nullable=False)
    feed_session_key = Column(String(128))
    refresh_count = Column(Integer, nullable=False, server_default=text("0"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    rank = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    source = Column(String(50), nullable=False)
    source_scores = Column(JSON, nullable=False, server_default=text("'{}'"))
    explanation_tags = Column(JSON)
    ontology_build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="SET NULL"))
    engine_version = Column(String(50), nullable=False)
    candidate_stage = Column(String(30), nullable=False)
    exposure_state = Column(String(30), nullable=False, server_default=text("'not_exposed'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class RecommendationFeedEvent(Base):
    __tablename__ = "recommendation_feed_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, nullable=False)
    request_id = Column(String(64), nullable=False)
    feed_session_key = Column(String(128))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    recommendation_id = Column(Integer, ForeignKey("ontology_recommendations.id", ondelete="SET NULL"))
    ontology_build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="SET NULL"))
    engine = Column(String(50), nullable=False)
    engine_version = Column(String(50), nullable=False)
    event_type = Column(String(30), nullable=False)
    rank_at_event = Column(Integer)
    score_at_event = Column(Float)
    dwell_ms = Column(Integer)
    refresh_count = Column(Integer, nullable=False, server_default=text("0"))
    source_scores_snapshot = Column(JSON)
    profile_snapshot = Column(JSON)
    context = Column(JSON)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
