# models/mapping.py
from sqlalchemy import Table, Column, Integer, Boolean, DateTime, ForeignKey, func, text
from sqlalchemy.orm import relationship
from app.db.base import Base

# --- 순수 다대다(N:M) 매핑 테이블 ---
movie_genres = Table(
    "movie_genres",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)
)

movie_otts = Table(
    "movie_otts",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("ott_id", Integer, ForeignKey("otts.id", ondelete="CASCADE"), primary_key=True)
)

movie_actors = Table(
    "movie_actors",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("actor_id", Integer, ForeignKey("actors.id", ondelete="CASCADE"), primary_key=True)
)

movie_keywords = Table(
    "movie_keywords",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("keyword_id", Integer, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True)
)

user_genres = Table(
    "user_genres",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)
)

user_otts = Table(
    "user_otts",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("ott_id", Integer, ForeignKey("otts.id", ondelete="CASCADE"), primary_key=True)
)

user_favorite_movies = Table(
    "user_favorite_movies",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
)

post_hashtags = Table(
    "post_hashtags",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column("hashtag_id", Integer, ForeignKey("hashtags.id", ondelete="CASCADE"), primary_key=True)
)

likes = Table(
    "likes",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("post_id", Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
)

# --- 추가 컬럼이 있는 다대다(N:M) 연결 모델 ---

class UserInteraction(Base):
    __tablename__ = "user_interactions"
    
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    is_pinned = Column(Boolean, server_default=text("false"), nullable=False)
    is_watched = Column(Boolean, server_default=text("false"), nullable=False)
    is_passed = Column(Boolean, server_default=text("false"), nullable=False)

    user = relationship("User", back_populates="interactions")
    movie = relationship("Movie", back_populates="interactions")


class PlaylistMovie(Base):
    __tablename__ = "playlist_movies"
    
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    playlist = relationship("Playlist", back_populates="playlist_movies")
    movie = relationship("Movie", back_populates="playlist_movies")