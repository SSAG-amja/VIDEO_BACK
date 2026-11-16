from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, Identity, func
from sqlalchemy.orm import relationship
from app.db.base import Base

class Reply(Base):
    __tablename__ = "replies"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # 2026.08.14 임재준
    # 대댓글을 위한 부모 댓글 id 컬럼을 추가한다.
    parent_id = Column(Integer, ForeignKey("replies.id", ondelete="CASCADE"), nullable=True)

    user = relationship("User", back_populates="replies")
    post = relationship("Post", back_populates="replies")

    # 2026.08.14 임재준
    # 대댓글 자기참조 관계 및 댓글 좋아요 관계를 정의한다.
    parent = relationship("Reply", remote_side=[id], back_populates="children")
    children = relationship("Reply", back_populates="parent", cascade="all, delete-orphan")
    liked_by = relationship("User", secondary="reply_likes", backref="liked_replies")