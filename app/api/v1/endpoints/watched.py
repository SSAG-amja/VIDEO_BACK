from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import watched as watched_crud
from app.models import user as user_model
from app.schemas import watched as watched_schema

router = APIRouter()


# 2026.05.13 박현식
# 현재 사용자의 본 영화 목록을 조회한다.
@router.get("", response_model=watched_schema.WatchedMovieListResponse)
def read_watched_movies(
    limit: int = Query(20, ge=1, le=100),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return watched_crud.get_watched_movies(db, current_user.id, limit)


# 2026.05.13 박현식
# 현재 사용자의 모든 본 영화 상태를 해제한다.
@router.delete("/all", response_model=watched_schema.WatchedCountResponse)
def delete_all_watched_movies(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    count = watched_crud.clear_watched_movies(db, current_user.id)
    return {"message": "본 영화 목록이 초기화되었습니다.", "count": count}


# 2026.05.13 박현식
# 특정 영화의 본 영화 상태를 해제한다.
@router.delete("", response_model=watched_schema.WatchedMovieMutationResponse)
def delete_watched_movie(
    movie_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = watched_crud.delete_watched_movie(db, current_user.id, movie_id)
    return {"message": "본 영화 목록에서 삭제되었습니다.", "data": movie}
