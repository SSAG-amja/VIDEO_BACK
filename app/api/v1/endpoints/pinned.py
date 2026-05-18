from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import pinned as pinned_crud
from app.models import user as user_model
from app.schemas import pinned as pinned_schema

router = APIRouter()


# 2026.05.13 박현식
# 현재 사용자의 Pin 영화 목록을 조회한다.
@router.get("", response_model=pinned_schema.PinnedMovieListResponse)
def read_pinned_movies(
    limit: int = Query(20, ge=1, le=100),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return pinned_crud.get_pinned_movies(db, current_user.id, limit)


# 2026.05.13 박현식
# 현재 사용자의 모든 Pin 상태를 해제한다.
@router.delete("/all", response_model=pinned_schema.PinnedCountResponse)
def delete_all_pinned_movies(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    count = pinned_crud.clear_pinned_movies(db, current_user.id)
    return {"message": "핀 보관함이 초기화되었습니다.", "count": count}


# 2026.05.13 박현식
# 특정 영화의 Pin 상태를 해제한다.
@router.delete("", response_model=pinned_schema.PinnedMovieMutationResponse)
def delete_pinned_movie(
    movie_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = pinned_crud.delete_pinned_movie(db, current_user.id, movie_id)
    return {"message": "핀 보관함에서 삭제되었습니다.", "data": movie}
