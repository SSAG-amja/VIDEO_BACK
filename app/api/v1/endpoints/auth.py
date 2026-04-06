# 사용자 인증 관련 API 엔드포인트 정의
# 로그인 로그아웃 회원가입 등

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session  # requests.Session 대신 sqlalchemy.orm.Session 사용

from app.schemas.token import Token
from app.core.config import settings 
from app.core import security
from app.schemas.token import Token
from app.schemas import user as user_schema

from app.api import deps
from app.api.deps import get_db
from app.crud import user as crud_user
from app.crud import user as user_crud

router = APIRouter()

# 260405 김광원
# 회원가입시 이메일, 닉네임 체크
@router.post("/signup", response_model=user_schema.UserResponse)
def create_user(
    *,
    db: Session = Depends(deps.get_db),
    user_in: user_schema.UserCreate
):
    if crud_user.get_active_user_by_email(db, email=user_in.email):
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    if user_in.nickname and crud_user.get_user_by_nickname(db, nickname=user_in.nickname):
        raise HTTPException(status_code=400, detail="이미 존재하는 닉네임입니다.")
    return user_crud.create_user(db, obj_in=user_in)

# 260405 김광원
# 주석 및 리팩토링
@router.post("/signin", response_model=Token)
def signin(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends() # Swagger Authorize 활성화
) -> Any:
    user = crud_user.get_active_user_by_email(db, email=form_data.username)
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email, password가 일치하지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = security.create_access_token(
        user.id,
        expires_delta=timedelta(minutes=settings.TOKEN_EXP_TIME),
    )

    return {
        "access_token": access_token,
        "token_type" : "bearer",
        "is_onboarding_completed": user.is_onboarding_completed,
    }

@router.post("/signout")
def signout():
    return {"message": "로그아웃 완료"}