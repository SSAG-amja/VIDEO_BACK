from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Identity, text
from sqlalchemy.orm import relationship
from app.db.base import Base

class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    title = Column(String(255), nullable=False)
    is_public = Column(Boolean, server_default=text("true"), nullable=False)

    user = relationship("User", back_populates="playlists")
    playlist_movies = relationship("PlaylistMovie", back_populates="playlist", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="playlist", cascade="all, delete-orphan")