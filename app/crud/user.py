# app/crud/user.py
from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash

def create_user(db: Session, *, obj_in: UserCreate) -> User:
    hashed_password = get_password_hash(obj_in.password)
    db_obj = User(
        email=obj_in.email,
        hashed_password=hashed_password,
        nickname=obj_in.nickname,
        is_active=True
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()

# 20260307 박현식: get_current_user에서 유저를 조회하기 위해 필수인 함수 추가
def get_user_by_id(db: Session, id: int):
    return db.query(User).filter(User.id == id).first()