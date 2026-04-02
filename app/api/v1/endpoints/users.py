# 사용자 관련 처리 로직
# 정보 수정, 조회, 온보딩 데이터 저장, OTT 조회, 수정

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas import user as user_schema
from app import models

router = APIRouter()

# [GET] 내 정보 확인 API
@router.get("/me", response_model=user_schema.UserResponse)
def read_user_me(
    # deps.get_current_user가 내부적으로 crud_user.get_user_by_id를 호출합니다.
    current_user: models.User = Depends(deps.get_current_user)
):
    return current_user

# 내 정보 수정
# @router.patch("/me')
# def update_user_me():

# 온보딩 데이터 저장
# @router.post("/me/onboarding")
# def onboarding_user_me():

# OTT 정보 조회
# @router.get("/me/otts")
# def otts_user_me():

# OTT 정보 수정
# @router.put("/me/otts")
# def update_otts_user_me():