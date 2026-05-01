from pydantic import BaseModel, EmailStr, Field, ConfigDict, SecretStr
from datetime import date, datetime
from typing import Optional, Literal
from typing import List

from app.schemas.movie import OTT, Genre, Movie

# 20260305 박현식
# 공통 유저 데이터 필드 정의 (Pydantic 모델)
class UserBase(BaseModel):
    email: EmailStr
    nickname: str = Field(None, max_length=10, description="닉네임은 최대 10자까지 허용됩니다.")

# API 응답 시 내보내는 데이터 규격
class UserInfoResponse(UserBase):
    user_id: int = Field(validation_alias="id")
    birth_date: date
    gender: Literal['M', 'F', 'U']
    is_onboarding_completed: bool
    otts: List[OTT] = Field(default_factory=list)
    genres: List[Genre] = Field(default_factory=list)
    favorite_movies: List[Movie] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

# 260404 김광원 
# 회원정보 수정 시 요청받는 데이터 규격 (현재는 닉네임만 수정 가능하도록 설정)
class UserInfoUpdate(BaseModel):
    nickname: Optional[str] = None

class UserPasswordUpdate(BaseModel):
    new_password: SecretStr = Field(..., min_length=8, description="새 비밀번호는 8자리 이상이어야 합니다.")
    new_password_confirm: SecretStr = Field(..., min_length=8, description="새 비밀번호 확인은 8자리 이상이어야 합니다.")