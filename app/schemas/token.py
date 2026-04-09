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