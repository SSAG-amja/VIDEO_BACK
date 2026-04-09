from pydantic import BaseModel, EmailStr, Field
from datetime import date, datetime
from typing import Optional, Literal
from typing import List

# 20260305 박현식
# 공통 유저 데이터 필드 정의 (Pydantic 모델)
class UserBase(BaseModel):
    email: EmailStr

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

# 회원가입 시 요청받는 데이터 규격
class UserCreate(UserBase):
    password: str = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")
    password_check: str
    birth_date: date
    gender: Literal['M', 'F', 'U'] = Field(..., description="M: 남성, F: 여성, U: 기타")
    nickname: str = Field(None, max_length=10, description="닉네임은 최대 10자까지 허용됩니다.")


# 260404 김광원 
# 회원정보 수정 시 요청받는 데이터 규격 (현재는 닉네임만 수정 가능하도록 설정)
class UserUpdate(BaseModel):
    nickname: Optional[str] = None

# 260404 김광원
# 사용자 온보딩 완료 시 요청받는 데이터 규격 (선호 OTT, 장르, 인생 영화 정보 포함)
class UserUpdateOtts(BaseModel):
    ott_ids: List[int] = Field(..., description="구독 중인 OTT ID 리스트")

class UserUpdateGenres(BaseModel):
    genre_ids: List[int] = Field(..., description="선호하는 장르 ID 리스트")

class UserUpdateMovies(BaseModel):
    movie_ids: List[int] = Field(..., description="인생 영화 ID 리스트")