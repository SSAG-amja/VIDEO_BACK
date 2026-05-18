from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, Identity, func
from sqlalchemy.orm import relationship
from app.db.base import Base

class Reply(Base):
    __tablename__ = "replies"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"))
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="replies")
    post = relationship("Post", back_populates="replies")