# app/schemas/token.py
from typing import Optional
from pydantic import BaseModel

# 20260307 박현식: 토큰 응답 규격 정의
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    # 토큰 안에 담긴 유저 ID(sub)를 검증할 때 사용
    id: Optional[str] = None

# 260407 김광원
# 로그인시 온보딩 완료 여부도 함께 반환하기 위한 응답 모델
# [회의필요] : 어디에 둬야할지, 혹은 어떻게 login시 해당 정보 줄지 (GET /me 로 해서 그냥 가져가게 할지)
class LoginResponse(Token):
    is_onboarding_completed: bool