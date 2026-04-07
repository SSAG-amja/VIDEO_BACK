from sqlalchemy import Column, Integer, String, Identity
from app.db.base import Base
from sqlalchemy.orm import relationship

# 260406 김광원
# [임시] OTT 모델 추가
class OTTs(Base):
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    logo_url = Column(String(255), nullable=True)
    movies = relationship("Movies", secondary="movie_otts", back_populates="otts")