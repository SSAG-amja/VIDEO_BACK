from sqlalchemy import Column, Integer, String, Identity
from app.db.base import Base 
from sqlalchemy.orm import relationship

# 260406 김광원
# [임시] Genre 모델 추가
class Genres(Base):
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    movies = relationship("Movies", secondary="movie_genres", back_populates="genres")

    