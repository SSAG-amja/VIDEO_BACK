from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Identity, func
from sqlalchemy.orm import relationship
from app.db.base import Base


# 2026.08.14 임재준
# 게시글에 첨부되는 투표(Poll) 테이블 정의
class Poll(Base):
    __tablename__ = "polls"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, unique=True)
    question = Column(String(255), nullable=True)
    is_closed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    post = relationship("Post", back_populates="poll")
    options = relationship("PollOption", back_populates="poll", cascade="all, delete-orphan", order_by="PollOption.id")
    votes = relationship("PollVote", back_populates="poll", cascade="all, delete-orphan")


# 2026.08.14 임재준
# 투표의 개별 선택지(Poll Option) 테이블 정의
class PollOption(Base):
    __tablename__ = "poll_options"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"), nullable=False)
    text = Column(String(255), nullable=False)

    poll = relationship("Poll", back_populates="options")
    votes = relationship("PollVote", back_populates="option", cascade="all, delete-orphan")


# 2026.08.14 임재준
# 사용자의 투표 참여 내역 테이블 정의 (1인 1투표 제한)
class PollVote(Base):
    __tablename__ = "poll_votes"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    poll_id = Column(Integer, ForeignKey("polls.id", ondelete="CASCADE"), nullable=False)
    option_id = Column(Integer, ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    poll = relationship("Poll", back_populates="votes")
    option = relationship("PollOption", back_populates="votes")
    user = relationship("User")