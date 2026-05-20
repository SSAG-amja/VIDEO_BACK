# app/api/v1/endpoints/user.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas import user as user_schema
from app.crud import user as user_crud
from app import models  # app/models/__init__.py에 User가 임포트되어 있어야 함

router = APIRouter()

# [POST] 회원가입 API
@router.post("/signin", response_model=user_schema.UserResponse)
def create_user(
    *,
    db: Session = Depends(deps.get_db),
    user_in: user_schema.UserCreate
):
    user = user_crud.get_user_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    return user_crud.create_user(db, obj_in=user_in)

# [GET] 내 정보 확인 API
@router.get("/me", response_model=user_schema.UserResponse)
def read_user_me(
    # deps.get_current_user가 내부적으로 crud_user.get_user_by_id를 호출합니다.
    current_user: models.User = Depends(deps.get_current_user)
):
    return current_user