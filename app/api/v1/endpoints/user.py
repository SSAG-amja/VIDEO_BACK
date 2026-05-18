# 사용자 관련 처리 로직
# 정보 수정, 조회, 온보딩 데이터 저장, OTT 조회, 수정

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.models import user as user_model

from app.api import deps

from app.schemas import user as user_schema
from app.schemas import auth as auth_schema

from app.crud import user as user_crud

router = APIRouter()

# 260501 김광원
# 사용자 정보 조회
@router.get("/me", response_model=user_schema.UserInfoResponse)
def read_user_me(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    user_profile = user_crud.get_user_with_preferences(db, user_id=current_user.id)
    if not user_profile:
        raise HTTPException(status_code=404, detail="User not found")
    return user_profile

# 2026.05.13 박현식
# 현재 로그인한 사용자의 개인정보를 수정하고 닉네임 중복으로 인한 응답 계약 충돌을 방지한다.
@router.patch("/me")
def update_user_me(
    request: user_schema.UserInfoUpdate,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    if request.nickname and request.nickname != current_user.nickname:
        existing_user = user_crud.get_user_by_nickname(db, nickname=request.nickname)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 닉네임입니다.")

    user = user_crud.update_user_profile(db, user=current_user, obj_in=request)
    return {
        "message": "회원 정보가 수정되었습니다.",
        "data": {
            "nickname": user.nickname,
            "birth_date": user.birth_date,
            "gender": user.gender,
        },
    }

# 2026.05.13 박현식
# 비밀번호 변경 요청을 받아 새 비밀번호를 해시로 저장하고 성공 메시지만 반환한다.
@router.patch("/me/new-password")
def update_user_password(
    request: user_schema.UserPasswordUpdate,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    user_crud.update_user_password(db, user=current_user, obj_in=request)
    return {"message": "비밀번호가 변경되었습니다."}

# 260501 김광원
# OTT 수정
@router.put("/user/otts")
def update_otts(
    request: user_schema.UserOttUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    try:
        user_crud.update_user_otts(db, user=current_user, ott_ids=request.ott_ids)
        return {"message": "성공적으로 업데이트되었습니다."}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )

# 260501 김광원
# 선호 장르 수정
@router.put("/user/genres")
def update_genres(
    request: user_schema.UserGenreUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    try:
        user_crud.update_user_genres(db, user=current_user, genre_ids=request.genre_ids)
        return {"message": "성공적으로 업데이트되었습니다."}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )


# 260501 김광원
# 선호 영화 수정
@router.put("/user/favorite-movies")
def update_favorite_movies(
    request: user_schema.UserMovieUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db)
):
    try:
        user_crud.update_user_favorite_movies(db, user=current_user, movie_ids=request.movie_ids)
        user_crud.update_user_onboarding_status(db, user=current_user, is_completed=True)
        return {"message": "성공적으로 업데이트되었습니다."}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )
