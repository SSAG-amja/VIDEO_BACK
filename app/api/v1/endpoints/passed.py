from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import passed as passed_crud
from app.models import user as user_model
from app.schemas import passed as passed_schema

router = APIRouter()


# 2026.05.13 박현식
# 현재 사용자의 관심없음 영화 목록을 조회한다.
@router.get("", response_model=passed_schema.PassedMovieListResponse)
def read_passed_movies(
    limit: int = Query(20, ge=1, le=100),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return passed_crud.get_passed_movies(db, current_user.id, limit)


# 2026.05.13 박현식
# 현재 사용자의 모든 관심없음 상태를 해제한다.
@router.delete("/all", response_model=passed_schema.PassedCountResponse)
def delete_all_passed_movies(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    count = passed_crud.clear_passed_movies(db, current_user.id)
    return {"message": "관심없음 목록이 초기화되었습니다.", "count": count}


# 2026.05.13 박현식
# 특정 영화의 관심없음 상태를 해제한다.
@router.delete("", response_model=passed_schema.PassedMovieMutationResponse)
def delete_passed_movie(
    movie_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = passed_crud.delete_passed_movie(db, current_user.id, movie_id)
    return {"message": "관심없음 목록에서 삭제되었습니다.", "data": movie}
