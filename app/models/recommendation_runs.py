from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func, text

from app.db.base import Base


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    run_id = Column(String(64), primary_key=True)
    engine = Column(String(50), nullable=False)
    engine_version = Column(String(50), nullable=False)
    ontology_build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="SET NULL"))
    run_type = Column(String(30), nullable=False)
    config_snapshot = Column(JSON, nullable=False, server_default=text("'{}'"))
    status = Column(String(30), nullable=False)
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime)
    elapsed_time = Column(Float)
    processed_user_count = Column(Integer, nullable=False, server_default=text("0"))
    generated_candidate_count = Column(Integer, nullable=False, server_default=text("0"))
    source_counts = Column(JSON)
    fallback_ratio = Column(Float)
    failure_reason = Column(Text)
