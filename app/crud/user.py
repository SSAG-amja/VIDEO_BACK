# app/crud/user.py
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select
from typing import List

from app.models import user as user_model
from app.models import movie as movie_model
from app.models import genre as genre_model
from app.models import ott as ott_model

from app.schemas import auth as auth_schema
from app.schemas import user as user_schema

from app.core.security import get_password_hash

def create_user(db: Session, *, obj_in: auth_schema.SignUpRequest) -> user_model.User:
    hashed_password = get_password_hash(obj_in.password.get_secret_value())
    db_obj = user_model.User(
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
def get_active_user_by_email(db: Session, email: str) -> user_model.User | None:
    stmt = select(user_model.User).where(
        user_model.User.email == email, 
        user_model.User.deleted_at.is_(None)
    )
    return db.scalars(stmt).first()


# 20260307 박현식
# get_current_user에서 유저를 조회하기 위해 필수인 함수 추가
def get_user_by_id(db: Session, id: int) -> user_model.User | None:   # 리프레시 토큰 추가시 할당 : refresh_token: str | None):
    stmt = select(user_model.User).where(user_model.User.id == id)
    user = db.scalars(stmt).first()
    
    # 나중에 리프레시 토큰 적용시 주석해제
    # if user and refresh_token:
    #    user.refresh_token = refresh_token
    #    db.commit()
    
    return user

# 260405 김광원
# 닉네임 중복 검사 위한 조회(소프트 딜리트 유저 제외)
def get_user_by_nickname(db: Session, nickname: str) -> user_model.User | None:
    stmt = select(user_model.User).where(
        user_model.User.nickname == nickname,
        user_model.User.deleted_at.is_(None)
    )
    return db.scalars(stmt).first()

# 260501 김광원
# 유저 선호 정보 조회 (OTT, 장르, 영화)
def get_user_with_preferences(db: Session, user_id: int) -> user_model.User | None:
    stmt = select(user_model.User).where(
        user_model.User.id == user_id,
        user_model.User.deleted_at.is_(None)  # 프로필 조회 시에도 탈퇴한 유저는 제외하는 것이 안전합니다.
    ).options(
        selectinload(user_model.User.otts),
        selectinload(user_model.User.genres),
        selectinload(user_model.User.favorite_movies)
    )
    return db.scalars(stmt).first()

# 260501 김광원
# OTT 입력 (scalars사용시 id값만)
def update_user_otts(db: Session, user: user_model.User, ott_ids: List[int]) -> user_model.User:
    if ott_ids:
        unique_ids = list(set(ott_ids))
        stmt = select(ott_model.Ott).where(ott_model.Ott.tmdb_id.in_(unique_ids))
        valid_otts = db.scalars(stmt).all()
        
        if len(valid_otts) != len(unique_ids):
            raise ValueError("존재하지 않는 OTT ID가 포함되어 있습니다.")
        user.otts = valid_otts
    else:
        user.otts = []
        
    db.commit()
    return user

# 260501 김광원
# 장르 입력
def update_user_genres(db: Session, user: user_model.User, genre_ids: List[int]) -> user_model.User:
    if genre_ids:
        unique_ids = list(set(genre_ids))
        stmt = select(genre_model.Genre).where(genre_model.Genre.tmdb_id.in_(unique_ids))
        valid_genres = db.scalars(stmt).all()
        
        if len(valid_genres) != len(unique_ids):
            raise ValueError("존재하지 않는 장르 ID가 포함되어 있습니다.")
        user.genres = valid_genres
    else:
        user.genres = []
        
    db.commit()
    return user

# 260501 김광원
# 영화 입력
def update_user_favorite_movies(db: Session, user: user_model.User, movie_ids: List[int]) -> user_model.User:
    if movie_ids:
        unique_ids = list(set(movie_ids))
        stmt = select(movie_model.Movie).where(movie_model.Movie.tmdb_id.in_(unique_ids))
        valid_movies = db.scalars(stmt).all()
        
        if len(valid_movies) != len(unique_ids):
            raise ValueError("존재하지 않는 영화 ID가 포함되어 있습니다.")
        user.favorite_movies = valid_movies
    else:
        user.favorite_movies = []
        
    db.commit()
    return user

def update_user_onboarding_status(db: Session, user: user_model.User, is_completed: bool) -> user_model.User:
    user.is_onboarding_completed = is_completed
    db.commit()
    return user

# 2026.05.13 박현식
# 사용자의 개인정보 수정 가능 필드만 갱신한다.
def update_user_profile(db: Session, user: user_model.User, obj_in: user_schema.UserInfoUpdate) -> user_model.User:
    update_data = obj_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user

# 2026.05.13 박현식
# 새 비밀번호를 해시로 변환해 사용자 계정에 저장한다.
def update_user_password(db: Session, user: user_model.User, obj_in: user_schema.UserPasswordUpdate) -> user_model.User:
    user.hashed_password = get_password_hash(obj_in.new_password.get_secret_value())
    db.commit()
    db.refresh(user)
    return user
