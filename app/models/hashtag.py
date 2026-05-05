from sqlalchemy import Column, Integer, String, Identity
from sqlalchemy.orm import relationship
from app.db.base import Base

class Hashtag(Base):
    __tablename__ = "hashtags"
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    
    posts = relationship("Post", secondary="post_hashtags", back_populates="hashtags")