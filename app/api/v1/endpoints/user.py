# 사용자 관련 처리 로직
# 정보 수정, 조회, 온보딩 데이터 저장, OTT 조회, 수정

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.models import user

from app.api import deps

from app.schemas import user as user_schema
from app.schemas import auth as auth_schema
from app.crud import user as user_crud


router = APIRouter()

# [GET] 내 정보 확인 API
@router.get("/me", response_model=user_schema.UserResponse)
def read_user_me(
    current_user: user.User = Depends(deps.get_current_user)
):
    return current_user

# 내 정보 수정
# @router.patch("/me", response_model=user_schema.UserResponse)
# def update_user_me():

# 260410 김광원
# 온보딩 ott
@router.post("/me/ott", response_model=user_schema.UserUpdateOtts)
def update_user_otts(
    *,
    current_user: user.User = Depends(deps.get_current_user),
    otts_in: user_schema.UserUpdateOtts
):
    return

@router.post("/me/genres", response_model=user_schema.UserUpdateGenres)
def update_user_genres(
    *,
    current_user: user.User = Depends(deps.get_current_user),
    genres_in: user_schema.UserUpdateGenres
):

# 260409 김광원
# OTT 정보 저장

# 260409 김광원
# favorite_vovei 저장
# @router.get("/me/otts")
# def otts_user_me():

# OTT 정보 수정
# @router.put("/me/otts")
# def update_otts_user_me():