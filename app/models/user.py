from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, Identity, func, text
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    refresh_token = Column(String(255))
    hashed_password = Column(String(255), nullable=False)
    nickname = Column(String(10), unique=True)
    birth_date = Column(Date, nullable=False)
    gender = Column(String(1), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    deleted_at = Column(DateTime)
    is_onboarding_completed = Column(Boolean, default=False, server_default=text("false"), nullable=False)

    genres = relationship("Genre", secondary="user_genres", back_populates="users")
    otts = relationship("Ott", secondary="user_otts", back_populates="users")
    favorite_movies = relationship("Movie", secondary="user_favorite_movies", back_populates="favorited_by")
    
    interactions = relationship("UserInteraction", back_populates="user", cascade="all, delete-orphan")
    playlists = relationship("Playlist", back_populates="user", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="user", cascade="all, delete-orphan")
    replies = relationship("Reply", back_populates="user", cascade="all, delete-orphan")
    liked_posts = relationship("Post", secondary="likes", back_populates="liked_by")