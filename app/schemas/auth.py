from typing import Optional
from pydantic import BaseModel
from token import Token

# 260407 김광원
# 로그인시 온보딩 완료 여부도 함께 반환하기 위한 응답 모델
class LoginResponse(Token):
    is_onboarding_completed: bool