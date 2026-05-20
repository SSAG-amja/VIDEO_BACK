# app/models/user.py
from sqlalchemy import Column, Integer, String, Boolean
# 이전 단계에서 만든 base_class를 사용합니다.
from app.db.base_class import Base 

# 20260305 박현식
# 본인 파일 내부이므로 import User 구문은 반드시 삭제해야 합니다.
class User(Base):
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    nickname = Column(String, index=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean(), default=True)
    is_onboarding_completed = Column(Boolean(), default=False)