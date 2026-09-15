from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func, text

from app.db.base import Base


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "engine", "rank", name="uq_recommendations_user_engine_rank"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True, index=True)
    engine = Column(String(20), primary_key=True, nullable=False, default="v1", server_default="v1")
    score = Column(Float, nullable=False)
    rank = Column(Integer, nullable=False)
    source = Column(String(50), nullable=True)
    source_scores = Column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    created_at = Column(DateTime, server_default=func.now())
