from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Identity, func
from sqlalchemy.orm import relationship
from app.db.base import Base


# 2026.08.22 임재준
# 게시글, 댓글 및 영화 정보 신고 내역 테이블 정의
class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    reporter_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_type = Column(String(20), nullable=False)  # "post", "reply", "movie"
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=True)
    reply_id = Column(Integer, ForeignKey("replies.id", ondelete="CASCADE"), nullable=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=True)
    reason = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    reporter = relationship("User")
    post = relationship("Post")
    reply = relationship("Reply")
    movie = relationship("Movie")
