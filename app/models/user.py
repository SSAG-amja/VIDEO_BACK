# app/models/user.py
from sqlalchemy import Column, Integer, String, Boolean, Identity, Date, DateTime
from app.db.base import Base 
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

# postgres : user 예약어여서 users로 변경
class Users(Base):
    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    email = Column(String(100), unique=True, index=True, nullable=False)
    refresh_token = Column(String(255), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    nickname = Column(String(10), unique=True, index=True, nullable=True)
    birth_date = Column(Date, nullable=False)
    gender = Column(String(1), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    is_onboarding_completed = Column(Boolean(), default=False)

    # 실제 컬럼이 아닌 관계 설정
    # relationship(타겟모델, 매핑테이블, 양방향 연결 설정)
    # 양방향 참조 개발 가능성 
    genres = relationship("Genres", secondary="user_genres")
    otts = relationship("OTTs", secondary="user_otts")
    favorite_movies = relationship("Movies", secondary="user_favorite_movies")