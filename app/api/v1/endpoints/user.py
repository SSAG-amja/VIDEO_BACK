# 사용자 관련 처리 로직
# 정보 수정, 조회, 온보딩 데이터 저장, OTT 조회, 수정

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.models import user

from app.api import deps

from app.schemas import user as user_schema
from app.crud import user as user_crud


router = APIRouter()

# [GET] 내 정보 확인 API
@router.get("/me", response_model=user_schema.UserResponse)
def read_user_me(
    current_user: user.Users = Depends(deps.get_current_user)
):
    return current_user

# 내 정보 수정
# @router.patch("/me", response_model=user_schema.UserResponse)
# def update_user_me():

# 260406 김광원
# 온보딩 데이터 저장
@router.post("/me/onboarding")
def onboarding_user_me(
    *,
    db: Session = Depends(deps.get_db),
    onboarding_in: user_schema.UserOnboarding,
    current_user = Depends(deps.get_current_user)
):
    '''
    # [회의 필요] : 혹시나 사용자가 기존 온보딩 데이터 바꿀 경우 해당 코드 필요없음
    if current_user.is_onboarding_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="이미 온보딩이 완료된 사용자입니다."
        )
    '''
    try:
        updated_user = user_crud.complete_onboarding(
            db, 
            user_id=current_user.id, 
            onboarding_data=onboarding_in
        )
        return {
            "message": "온보딩 정보가 성공적으로 저장되었습니다.",
            "is_onboarding_completed": updated_user.is_onboarding_completed
        }
    except Exception as e:
        # DB 제약 조건 위반 (존재하지 않는 OTT ID 등) 시 에러 처리
        db.rollback()
        print(f"❌ DB 저장 중 진짜 에러 발생: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="데이터 저장 중 오류가 발생했습니다. ID 값을 확인해주세요."
        )
    
# OTT 정보 조회
# @router.get("/me/otts")
# def otts_user_me():

# OTT 정보 수정
# @router.put("/me/otts")
# def update_otts_user_me():