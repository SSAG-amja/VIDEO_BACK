# app/api/v1/endpoints/login.py
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session  # requests.Session 대신 sqlalchemy.orm.Session 사용

from app.schemas.token import Token
from app.core.config import settings # VIDEO_BACK의 환경설정 객체 사용
from app.core import security
from app.schemas.token import Token

from app.api.deps import get_db
from app.crud import user as crud_user

router = APIRouter()

# 260117 김광원 (현식 리더 요청으로 VIDEO_BACK 이식)
# 로그인
@router.post("/", response_model=Token)
def signin(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends() # Swagger Authorize 활성화
) -> Any:
    # username 칸에 입력된 이메일로 유저를 찾습니다.
    user = crud_user.get_user_by_email(db, email=form_data.username)
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email, password가 일치하지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # VIDEO_BACK의 토큰 생성 로직 (MUSIC_BACK과 동일한 sub 구조)
    access_token = security.create_access_token(
        user.id,
        expires_delta=timedelta(minutes=int(settings.TOKEN_EXP_TIME)),
    )

    return {
        "access_token": access_token,
        "token_type" : "bearer",
    }