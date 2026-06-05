from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session  # requests.Session 대신 sqlalchemy.orm.Session 사용

from app.core.config import settings 
from app.core import security
from app.core.redis import get_redis
from app.schemas.auth import SignInResponse
from app.schemas import user as user_schema
from app.schemas import auth as auth_schema
from app.services.auth import email_verification

from app.api import deps
from app.crud import user as user_crud

router = APIRouter()

# 260405 김광원
# 회원가입시 이메일, 닉네임 체크
@router.post("/signup", response_model=auth_schema.SignUpResponse)
def create_user(
    *,
    db: Session = Depends(deps.get_db),
    user_in: auth_schema.SignUpRequest
):
    normalized_email = email_verification.normalize_email(user_in.email)
    user_in.email = normalized_email

    if user_crud.get_active_user_by_email(db, email=normalized_email):
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    if user_in.nickname and user_crud.get_user_by_nickname(db, nickname=user_in.nickname):
        raise HTTPException(status_code=400, detail="이미 존재하는 닉네임입니다.")

    try:
        email_verification.validate_signup_token(get_redis(), normalized_email, user_in.signup_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    user = user_crud.create_user(db, obj_in=user_in)
    if user is None:
        raise HTTPException(status_code=500, detail="회원가입에 실패했습니다.")
    try:
        email_verification.delete_signup_token(get_redis(), user_in.signup_token)
    except RuntimeError:
        pass
    return user

# 260405 김광원
# 주석 및 리팩토링
@router.post("/signin", response_model=auth_schema.SignInResponse)
def signin(
    db: Session = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends() # Swagger Authorize 활성화
) -> Any:
    user = user_crud.get_active_user_by_email(
        db,
        email=email_verification.normalize_email(form_data.username),
    )
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
        "is_onboarding_completed": user.is_onboarding_completed,
        "access_token": access_token,
        "token_type" : "bearer",
    }

# 2026.05.13 박현식
# 클라이언트 토큰 폐기 흐름을 위한 로그아웃 성공 메시지를 반환한다.
@router.post("/signout")
def signout():
    return {"message": "로그아웃 완료"}

# 2026.05.13 박현식
# 민감 정보 수정 전 현재 비밀번호가 맞는지 확인한다.
# 26.06.04 김광원
# 현재 비밀번호를 검증하고 비밀번호 변경에 사용할 임시 토큰을 발급한다.
@router.post("/verify-password", response_model=auth_schema.VerifyPasswordResponse)
def verify_password(
    request: auth_schema.VerifyPasswordRequest,
    current_user = Depends(deps.get_current_user)
):
    if not security.verify_password(request.current_password.get_secret_value(), current_user.hashed_password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 일치하지 않습니다.")
    try:
        password_change_token = email_verification.create_password_change_token(get_redis(), current_user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {
        "message": "비밀번호 변경 인증이 완료되었습니다.",
        "password_change_token": password_change_token,
    }


# 26.06.04 김광원
# 회원가입 전에 이메일 인증 코드를 발급하고 Redis에 만료 시간과 함께 저장한다.
@router.post("/email/send-code", response_model=auth_schema.EmailSendResponse)
def send_email_code(
    request: auth_schema.EmailSendRequest,
    db: Session = Depends(deps.get_db),
):
    email = email_verification.normalize_email(request.email)
    if user_crud.get_active_user_by_email(db, email=email):
        raise HTTPException(status_code=400, detail="이미 가입된 이메일")

    try:
        expires_in = email_verification.create_email_verification(get_redis(), email)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "message": "인증 코드가 발송되었습니다.",
        "expires_in": expires_in,
    }


# 26.06.04 김광원
# 이메일 인증 코드를 검증하고 회원가입에 사용할 임시 토큰을 발급한다.
@router.post("/email/verify-code", response_model=auth_schema.EmailVerifyResponse)
def verify_email_code(
    request: auth_schema.EmailVerifyRequest,
    db: Session = Depends(deps.get_db),
):
    email = email_verification.normalize_email(request.email)
    if user_crud.get_active_user_by_email(db, email=email):
        raise HTTPException(status_code=400, detail="이미 가입된 이메일")

    try:
        signup_token = email_verification.verify_email_code(get_redis(), email, request.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "message": "이메일 인증이 완료되었습니다.",
        "signup_token": signup_token,
    }


# 26.06.04 김광원
# 비밀번호 재설정 전에 가입된 이메일인지 확인한 뒤 인증 코드를 발급한다.
@router.post("/password-reset/send-code", response_model=auth_schema.EmailSendResponse)
def send_password_reset_code(
    request: auth_schema.EmailSendRequest,
    db: Session = Depends(deps.get_db),
):
    email = email_verification.normalize_email(request.email)
    user = user_crud.get_active_user_by_email(db, email=email)
    if not user:
        raise HTTPException(status_code=400, detail="가입되지 않은 이메일입니다.")

    try:
        expires_in = email_verification.create_password_reset_verification(get_redis(), email)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "message": "인증 코드가 발송되었습니다.",
        "expires_in": expires_in,
    }


# 26.06.04 김광원
# 비밀번호 재설정 인증 코드를 검증하고 재설정에 사용할 임시 토큰을 발급한다.
@router.post("/password-reset/verify-code", response_model=auth_schema.PasswordResetVerifyResponse)
def verify_password_reset_code(
    request: auth_schema.EmailVerifyRequest,
    db: Session = Depends(deps.get_db),
):
    email = email_verification.normalize_email(request.email)
    user = user_crud.get_active_user_by_email(db, email=email)
    if not user:
        raise HTTPException(status_code=400, detail="인증 코드가 만료되었거나 존재하지 않습니다.")

    try:
        reset_token = email_verification.verify_password_reset_code(get_redis(), email, request.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "message": "비밀번호 재설정 인증이 완료되었습니다.",
        "reset_token": reset_token,
    }


# 26.06.04 김광원
# 비밀번호 재설정 임시 토큰을 검증한 뒤 새 비밀번호로 변경한다.
@router.post("/password-reset/confirm")
def confirm_password_reset(
    request: auth_schema.PasswordResetConfirmRequest,
    db: Session = Depends(deps.get_db),
):
    email = email_verification.normalize_email(request.email)
    user = user_crud.get_active_user_by_email(db, email=email)
    if not user:
        raise HTTPException(status_code=400, detail="유효하지 않은 비밀번호 재설정 인증 정보입니다.")

    try:
        email_verification.validate_password_reset_token(get_redis(), email, request.reset_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    user_crud.set_user_password(db, user=user, password=request.new_password.get_secret_value())

    try:
        email_verification.delete_password_reset_token(get_redis(), request.reset_token)
    except RuntimeError:
        pass

    return {"message": "비밀번호가 재설정되었습니다."}
