# models/mapping.py
from sqlalchemy import Table, Column, Integer, Boolean, DateTime, ForeignKey, func, text, String
from sqlalchemy.orm import relationship
from app.db.base import Base

movie_genres = Table(
    "movie_genres",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)
)

movie_keywords = Table(
    "movie_keywords",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("keyword_id", Integer, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True)
)

movie_directors = Table(
    "movie_directors",
    Base.metadata,
    Column("movie_id", Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("director_id", Integer, ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
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

class MovieActor(Base):
    __tablename__ = "movie_actors"
    
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    actor_id = Column(Integer, ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    cast_name = Column(String(100))

    movie = relationship("Movie", back_populates="movie_actors")
    actor = relationship("People", back_populates="movie_actors")

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

class MovieOtt(Base):
    __tablename__ = "movie_otts"
    
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    ott_id = Column(Integer, ForeignKey("otts.id", ondelete="CASCADE"), primary_key=True)
    
    is_streaming = Column(Boolean, server_default=text("false"), nullable=False)
    is_rent = Column(Boolean, server_default=text("false"), nullable=False)
    is_buy = Column(Boolean, server_default=text("false"), nullable=False)

    movie = relationship("Movie", back_populates="movie_otts")
    ott = relationship("Ott", back_populates="movie_otts")