from sqlalchemy import Column, Integer, String, Text, Float, Date, BigInteger, Boolean
from app.db.base import Base
from sqlalchemy.orm import relationship

class Movies(Base):
    id = Column(Integer, primary_key=True, index=True)
    imdb_id = Column(String(50), index=True, nullable=True)
    title = Column(String(255), index=True, nullable=True)
    original_title = Column(String(255), nullable=True)
    original_language = Column(String(50), nullable=True)

    # 상세정보
    overview = Column(Text, nullable=True)
    keywords = Column(Text, nullable=True)
    director = Column(Text, nullable=True)
    
    # 종속성제거
    actor = Column(Text, nullable=True)

    # 수치 및 통계 데이터
    popularity = Column(Float, nullable=True)
    vote_average = Column(Float, nullable=True)
    vote_count = Column(Integer, nullable=True)
    release_date = Column(Date, nullable=True)
    runtime = Column(Integer, nullable=True)
    budget = Column(BigInteger, nullable=True)
    revenue = Column(BigInteger, nullable=True)

    # 상태 및 이미지
    adult = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), nullable=True)
    poster_path = Column(String(255), nullable=True)
    backdrop_path = Column(String(255), nullable=True)

    genres = relationship("Genres", secondary="movie_genres", back_populates="movies")
    otts = relationship("OTTs", secondary="movie_otts", back_populates="movies")
    favorited_by_users = relationship("Users", secondary="user_favorite_movies", back_populates="favorite_movies")
    
    # actor relationship
    actors = relationship("Actors", secondary="movie_actors", back_populates="movies")