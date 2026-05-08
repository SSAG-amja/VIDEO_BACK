from sqlalchemy import Column, Integer, String, Identity, Text 
from sqlalchemy.orm import relationship
from app.db.base import Base

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, index=True)
    name = Column(Text, unique=True, index=True, nullable=False)
    
    movies = relationship("Movie", secondary="movie_keywords", back_populates="keywords")