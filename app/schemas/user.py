from pydantic import BaseModel, EmailStr, Field, ConfigDict, SecretStr
from datetime import date, datetime
from typing import Optional, Literal
from typing import List

from app.schemas.movie import OTT, Genre, Movie

# 20260305 박현식
# 공통 유저 데이터 필드 정의 (Pydantic 모델)
class UserBase(BaseModel):
    email: EmailStr

# API 응답 시 내보내는 데이터 규격
class UserInfoResponse(UserBase, OTT):
    nickname: str
    birth_data: date
    gender: Literal['M', 'F', 'U']
    is_onboarding_completed: bool
    otts: List[OTT]
    genres: List[Genre]
    favorite_movies: List[Movie]

    model_config = ConfigDict(from_attributes=True)

# 260404 김광원 
# 회원정보 수정 시 요청받는 데이터 규격 (현재는 닉네임만 수정 가능하도록 설정)
class UserInfoUpdate(BaseModel):
    nickname: Optional[str] = None

class UserPasswordUpdate(BaseModel):
    new_password: SecretStr = Field(..., min_length=8, description="새 비밀번호는 8자리 이상이어야 합니다.")
    new_password_confirm: SecretStr = Field(..., min_length=8, description="새 비밀번호 확인은 8자리 이상이어야 합니다.")

# 260404 김광원
# 사용자 온보딩 완료 시 요청받는 데이터 규격 (선호 OTT, 장르, 인생 영화 정보 포함)
class UserUpdateOtts(BaseModel):
    ott_ids: List[int] = Field(..., description="구독 중인 OTT ID 리스트")

class UserUpdateGenres(BaseModel):
    genre_ids: List[int] = Field(..., description="선호하는 장르 ID 리스트")

class UserUpdateMovies(BaseModel):
    movie_ids: List[int] = Field(..., description="인생 영화 ID 리스트")