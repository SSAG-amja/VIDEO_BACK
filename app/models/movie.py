from sqlalchemy import Column, Integer, String, Boolean, Text, Float, BigInteger, Date, Identity, text
from sqlalchemy.orm import relationship
from app.db.base import Base

class Movie(Base):
    __tablename__ = "movies"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True)
    imdb_id = Column(String(50))
    title = Column(Text)
    title_ko = Column(String(255))
    original_title = Column(String(255))
    original_language = Column(String(50))
    overview = Column(Text)
    popularity = Column(Float)
    vote_average = Column(Float)
    vote_count = Column(Integer)
    release_date = Column(Date)
    runtime = Column(Integer)
    budget = Column(BigInteger)
    revenue = Column(BigInteger)
    adult = Column(Boolean, server_default=text("false"), nullable=False)
    status = Column(String(50))
    poster_path = Column(Text)
    backdrop_path = Column(Text)

    genres = relationship("Genre", secondary="movie_genres", back_populates="movies")
    movie_otts = relationship("MovieOtt", back_populates="movie", cascade="all, delete-orphan")

    movie_actors = relationship("MovieActor", back_populates="movie", cascade="all, delete-orphan")
    directors = relationship("People", secondary="movie_directors", back_populates="directed_movies")
    keywords = relationship("Keyword", secondary="movie_keywords", back_populates="movies")
    favorited_by = relationship("User", secondary="user_favorite_movies", back_populates="favorite_movies")
    
    interactions = relationship("UserInteraction", back_populates="movie", cascade="all, delete-orphan")
    playlist_movies = relationship("PlaylistMovie", back_populates="movie", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="movie", cascade="all, delete-orphan")