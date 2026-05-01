from app.schemas.token import Token
from app.schemas.user import UserBase
from pydantic import BaseModel, EmailStr, Field, SecretStr, ConfigDict
from datetime import date
from typing import Optional, Literal, List

# 260407 김광원
# 로그인시 온보딩 완료 여부도 함께 반환하기 위한 응답 모델
class SignInResponse(Token):
    is_onboarding_completed: bool

# 2604430 김광원
# 회원가입
class SignUpRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")
    password_confirm: SecretStr = Field(..., min_length=8, description="비밀번호 확인은 8자리 이상이어야 합니다.")
    birth_date: date
    gender: Literal['M', 'F', 'U'] = Field(..., description="M: 남성, F: 여성, U: 기타")
    nickname: str = Field(None, max_length=10, description="닉네임은 최대 10자까지 허용됩니다.")

class SignUpResponse(UserBase):
    message: str = "회원가입이 완료되었습니다."
    nickname: str
    model_config = ConfigDict(from_attributes=True) # SQLAlchemy 모델 객체를 Pydantic 모델로 자동 변환 허용


class VerifyPasswordRequest(BaseModel):
    current_password: SecretStr = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")