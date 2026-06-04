from app.schemas.token import Token
from app.schemas.user import UserBase
from pydantic import BaseModel, EmailStr, Field, SecretStr, ConfigDict, model_validator
from datetime import date
from typing import Optional, Literal, List

# 260407 김광원
# 로그인시 온보딩 완료 여부도 함께 반환하기 위한 응답 모델
class SignInResponse(Token):
    is_onboarding_completed: bool

# 2604430 김광원
# 회원가입
class SignUpRequest(UserBase):
    password: SecretStr = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")
    password_confirm: SecretStr = Field(..., min_length=8, description="비밀번호 확인은 8자리 이상이어야 합니다.")
    signup_token: str = Field(..., min_length=1, description="이메일 인증 완료 후 발급되는 임시 토큰")
    birth_date: date
    gender: Literal['M', 'F', 'U'] = Field(..., description="M: 남성, F: 여성, U: 기타")

    # 26.06.04 김광원
    # 회원가입 시 비밀번호와 비밀번호 확인이 일치하는지 검증한다.
    @model_validator(mode="after")
    def validate_password_match(self):
        if self.password.get_secret_value() != self.password_confirm.get_secret_value():
            raise ValueError("비밀번호와 비밀번호 확인이 일치하지 않습니다.")
        return self

class SignUpResponse(UserBase):
    message: str = "회원가입이 완료되었습니다."
    model_config = ConfigDict(from_attributes=True) # SQLAlchemy 모델 객체를 Pydantic 모델로 자동 변환 허용


class VerifyPasswordRequest(BaseModel):
    current_password: SecretStr = Field(..., min_length=8, description="비밀번호는 8자리 이상이어야 합니다.")

class EmailSendRequest(BaseModel):
    email: EmailStr

class EmailVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, description="인증 코드는 6자리여야 합니다.")


class EmailSendResponse(BaseModel):
    message: str
    expires_in: int


class EmailVerifyResponse(BaseModel):
    message: str
    signup_token: str
