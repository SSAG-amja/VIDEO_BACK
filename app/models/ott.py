from sqlalchemy import Column, Integer, String, Identity
from sqlalchemy.orm import relationship
from app.db.base import Base

class Ott(Base):
    __tablename__ = "otts"
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    
    movie_otts = relationship("MovieOtt", back_populates="ott", cascade="all, delete-orphan")
    users = relationship("User", secondary="user_otts", back_populates="otts")