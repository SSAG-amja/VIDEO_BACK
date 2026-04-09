from sqlalchemy import Column, Integer, Boolean, Text, DateTime, ForeignKey, Identity, func, text
from sqlalchemy.orm import relationship
from app.db.base import Base

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=True)
    is_playlist = Column(Boolean, server_default=text("false"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="posts")
    movie = relationship("Movie", back_populates="posts")
    playlist = relationship("Playlist", back_populates="posts")
    
    hashtags = relationship("Hashtag", secondary="post_hashtags", back_populates="posts")
    liked_by = relationship("User", secondary="likes", back_populates="liked_posts")
    replies = relationship("Reply", back_populates="post", cascade="all, delete-orphan")