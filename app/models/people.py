from sqlalchemy import Column, Integer, String, Identity
from sqlalchemy.orm import relationship
from app.db.base import Base

# 260501 김광원
# 배우 모델 추가
class People(Base):
    __tablename__ = "people"

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True)
    name = Column(String(100), nullable=False)
    name_ko = Column(String(100), nullable=True)
    
    movie_actors = relationship("MovieActor", back_populates="actor", cascade="all, delete-orphan")
    directed_movies = relationship("Movie", secondary="movie_directors", back_populates="directors")
