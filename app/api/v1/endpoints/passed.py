from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import passed as passed_crud
from app.crud import interaction as interaction_crud
from app.core.redis import get_redis
from app.models.mapping import UserInteraction
from app.models import user as user_model
from app.schemas import passed as passed_schema
from app.services.recsys.v1.interaction_cache import remove_blacklisted_movie_ids
from app.services.recsys.profile_change import mark_recommendation_profile_changed

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
    blacklist_removal_ids = passed_crud.load_unwatched_passed_movie_ids(db, current_user.id)
    count = passed_crud.clear_passed_movies(db, current_user.id)
    remove_blacklisted_movie_ids(get_redis(), current_user.id, blacklist_removal_ids)
    if count:
        mark_recommendation_profile_changed(get_redis(), current_user.id)
    return {"message": "관심없음 목록이 초기화되었습니다.", "count": count}


# 2026.05.13 박현식
# 특정 영화의 관심없음 상태를 해제한다.
@router.delete("", response_model=passed_schema.PassedMovieMutationResponse)
def delete_passed_movie(
    movie_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    internal_movie = interaction_crud.get_movie_by_tmdb_id(db, movie_id)
    interaction = db.get(UserInteraction, {"user_id": current_user.id, "movie_id": internal_movie.id})
    movie = passed_crud.delete_passed_movie(db, current_user.id, movie_id)
    if interaction is None or not interaction.is_watched:
        remove_blacklisted_movie_ids(get_redis(), current_user.id, {internal_movie.id})
    mark_recommendation_profile_changed(get_redis(), current_user.id)
    return {"message": "관심없음 목록에서 삭제되었습니다.", "data": movie}
