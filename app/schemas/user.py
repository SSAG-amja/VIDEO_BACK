from pydantic import BaseModel, EmailStr
from typing import Optional

# 20260305 박현식
# 공통 유저 데이터 필드 정의 (Pydantic 모델)
class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    nickname: Optional[str] = None

# 회원가입 시 요청받는 데이터 규격
class UserCreate(UserBase):
    email: EmailStr
    password: str

# API 응답 시 내보내는 데이터 규격
class UserResponse(UserBase):
    id: int
    is_onboarding_completed: bool

    class Config:
        # SQLAlchemy 모델 객체를 Pydantic 모델로 자동 변환 허용
        from_attributes = True