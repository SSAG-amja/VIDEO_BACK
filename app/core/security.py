from datetime import datetime, timedelta
from typing import Any, Union
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings

# 20260305 박현식
# 비밀번호 암호화를 위한 알고리즘 설정 (BCRYPT 사용)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 평문 비밀번호를 해시값으로 변환하여 DB에 안전하게 저장함
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# 입력받은 비밀번호와 DB의 해시값을 비교하여 일치 여부 확인
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# 사용자 인증 후 전송할 JWT 액세스 토큰 생성 로직
# .env의 SECRET_KEY와 ALGORITHM을 사용하여 서명함
def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        # 기본 만료 시간 설정 (예: 30분)
        expire = datetime.utcnow() + timedelta(minutes=60 * 24 * 7) # 임시 1주일
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt