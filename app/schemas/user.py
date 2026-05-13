from pydantic import BaseModel, EmailStr, Field, SecretStr, ConfigDict, model_validator
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

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# 2026.05.13 박현식
# 개인정보 수정 API에서 변경 가능한 필드만 선택적으로 받아 기존 사용자 정보에 반영한다.
class UserInfoUpdate(BaseModel):
    nickname: Optional[str] = Field(None, max_length=10)
    birth_date: Optional[date] = None
    gender: Optional[Literal['M', 'F', 'U']] = None

# 2026.05.13 박현식
# 새 비밀번호와 확인값을 함께 받아 평문 저장 없이 해시 갱신에 사용한다.
class UserPasswordUpdate(BaseModel):
    new_password: SecretStr = Field(..., min_length=8, description="새 비밀번호는 8자리 이상이어야 합니다.")
    new_password_confirm: SecretStr = Field(..., min_length=8, description="새 비밀번호 확인은 8자리 이상이어야 합니다.")

    # 2026.05.13 박현식
    # 새 비밀번호와 확인값이 같은지 검증한다.
    @model_validator(mode="after")
    def validate_password_match(self):
        if self.new_password.get_secret_value() != self.new_password_confirm.get_secret_value():
            raise ValueError("새 비밀번호와 비밀번호 확인이 일치하지 않습니다.")
        return self

class UserOttUpdateRequest(BaseModel):
    ott_ids: List[int] = Field(default_factory=list, description="업데이트할 OTT ID 목록")

class UserGenreUpdateRequest(BaseModel):
    genre_ids: List[int] = Field(default_factory=list, description="업데이트할 장르 ID 목록")

class UserMovieUpdateRequest(BaseModel):
    movie_ids: List[int] = Field(default_factory=list, description="업데이트할 영화 ID 목록")
