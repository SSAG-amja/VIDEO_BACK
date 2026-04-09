from sqlalchemy import Column, Integer, String, Identity
from sqlalchemy.orm import relationship
from app.db.base import Base

class Actor(Base):
    __tablename__ = "actors"
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    
    movies = relationship("Movie", secondary="movie_actors", back_populates="actors")