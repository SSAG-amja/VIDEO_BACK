from pydantic import BaseModel, EmailStr, Field
from datetime import date, datetime, timezone
from typing import Optional, Literal

# 20260305 박현식
# 공통 유저 데이터 필드 정의 (Pydantic 모델)
class UserBase(BaseModel):
    email: EmailStr

# 회원가입 시 요청받는 데이터 규격
class UserCreate(UserBase):
    password: str = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")
    birth_date: date
    gender: Literal['M', 'F', 'U'] = Field(..., description="M: 남성, F: 여성, U: 기타")
    nickname: str = Field(None, max_length=10, description="닉네임은 최대 10자까지 허용됩니다.")

# 260404 김광원 
# 회원정보 수정 시 요청받는 데이터 규격 (현재는 닉네임만 수정 가능하도록 설정)
class UserUpdate(BaseModel):
    nickname: Optional[str] = None

# 260404 김광원
# 온보딩 시 수집하는 선호 장르 등의 추가 정보 규격
class UserOnboarding(BaseModel):
    is_onboarding_completed: bool = True

    # 이름값이 아닌 id값으로 OTT와 장르를 관리할 경우, 아래 필드들은 list[int]로 변경 필요
    # Optionsal로 해놨고 나중에 default값은 어떤걸로 할지 정해야할듯
    ott_in_use: Optional[list[str]] = Field(None, description="사용 중인 OTT 목록")
    genre_preferences: Optional[list[str]] = Field(None, description="선호 장르 목록")

# API 응답 시 내보내는 데이터 규격
class UserResponse(UserBase):
    id: int
    birth_date: date
    gender: str
    created_at: datetime
    is_onboarding_completed: bool

    class Config:
        # SQLAlchemy 모델 객체를 Pydantic 모델로 자동 변환 허용
        from_attributes = True