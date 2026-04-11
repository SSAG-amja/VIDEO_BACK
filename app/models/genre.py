from sqlalchemy import Column, Integer, String, Identity
from sqlalchemy.orm import relationship
from app.db.base import Base

class Genre(Base):
    __tablename__ = "genres"
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True) 
    name = Column(String(50), unique=True, index=True, nullable=False)
    
    # Relationships
    movies = relationship("Movie", secondary="movie_genres", back_populates="genres")
    users = relationship("User", secondary="user_genres", back_populates="genres")