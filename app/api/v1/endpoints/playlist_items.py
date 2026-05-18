from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import playlist as playlist_crud
from app.models import user as user_model
from app.schemas import playlist as playlist_schema

router = APIRouter()


# 2026.05.13 박현식
# 특정 플레이리스트에 담긴 영화 목록을 조회한다.
@router.get("/{playlist_id}/movies", response_model=playlist_schema.MovieListResponse)
def read_playlist_movies(
    playlist_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return playlist_crud.get_playlist_movies(db, current_user.id, playlist_id)


# 2026.05.13 박현식
# 특정 플레이리스트에 영화를 추가한다.
@router.post("/{playlist_id}/movies", response_model=playlist_schema.MovieMutationResponse)
def add_playlist_movie(
    playlist_id: int,
    request: playlist_schema.PlaylistMovieRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = playlist_crud.add_playlist_movie(db, current_user.id, playlist_id, request)
    return {"message": "플레이리스트에 영화가 추가되었습니다.", "data": movie}


# 2026.05.13 박현식
# 특정 플레이리스트에서 영화 하나를 삭제한다.
@router.delete("/{playlist_id}/movies", response_model=playlist_schema.MovieMutationResponse)
def delete_playlist_movie(
    playlist_id: int,
    movie_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = playlist_crud.delete_playlist_movie(db, current_user.id, playlist_id, movie_id)
    return {"message": "플레이리스트에서 영화가 삭제되었습니다.", "data": movie}


# 2026.05.13 박현식
# 특정 플레이리스트 안의 모든 영화를 삭제한다.
@router.delete("/{playlist_id}/movies/all", response_model=playlist_schema.CountResponse)
def delete_all_playlist_movies(
    playlist_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    count = playlist_crud.delete_all_playlist_movies(db, current_user.id, playlist_id)
    return {"message": "플레이리스트의 모든 영화가 삭제되었습니다.", "count": count}
