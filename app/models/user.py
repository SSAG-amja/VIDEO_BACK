# app/models/user.py
from sqlalchemy import Column, Integer, String, Boolean, Identity, Date, DateTime
from app.db.base import Base 
from sqlalchemy.sql import func

class User(Base):
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