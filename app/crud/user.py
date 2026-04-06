# app/crud/user.py
from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash

# 260405 김광원
# is_active 제거
def create_user(db: Session, *, obj_in: UserCreate) -> User:
    hashed_password = get_password_hash(obj_in.password)
    db_obj = User(
        email=obj_in.email,
        hashed_password=hashed_password,
        nickname=obj_in.nickname,
        birth_date=obj_in.birth_date,
        gender=obj_in.gender
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


# 260404 김광원 
# 소프트 딜리트 유저 제외
def get_active_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(
        User.email == email, 
        User.deleted_at.is_(None)
    ).first()

# 20260307 박현식
# get_current_user에서 유저를 조회하기 위해 필수인 함수 추가
def get_user_by_id(db: Session, id: int):   # 리프레시 토큰 추가시 할당 : refresh_token: str | None):
    return db.query(User).filter(User.id == id).first()
    # 나중에 리프레시 토큰 적용시 주석해제
    # if user:
       # user.refresh_token = refresh_token
       # db.commit()

# 260405 김광원
# 닉네임 중복 검사 위한 조회(소프트 딜리트 유저 제외)
def get_user_by_nickname(db: Session, nickname: str) -> User | None:
    return db.query(User).filter(
        User.nickname == nickname,
        User.deleted_at.is_(None)
    ).first()