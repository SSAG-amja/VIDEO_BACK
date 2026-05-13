from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import playlist as playlist_crud
from app.models import user as user_model
from app.schemas import playlist as playlist_schema

router = APIRouter()


# 2026.05.13 박현식
# 현재 사용자의 플레이리스트 목록과 썸네일용 영화를 조회한다.
@router.get("", response_model=playlist_schema.PlaylistListResponse)
def read_playlists(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return playlist_crud.get_playlists(db, current_user.id)


# 2026.05.13 박현식
# 새 플레이리스트를 생성하고 생성된 DB id를 반환한다.
@router.post("", response_model=playlist_schema.PlaylistMutationResponse)
def create_playlist(
    request: playlist_schema.PlaylistCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    playlist = playlist_crud.create_playlist(db, current_user.id, request)
    return {"message": "플레이리스트가 생성되었습니다.", "data": playlist}


# 2026.05.13 박현식
# 플레이리스트 제목 또는 공개 여부를 수정한다.
@router.patch("", response_model=playlist_schema.PlaylistMutationResponse)
def update_playlist(
    request: playlist_schema.PlaylistUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    playlist = playlist_crud.update_playlist(db, current_user.id, request)
    return {"message": "플레이리스트가 수정되었습니다.", "data": playlist}


# 2026.05.13 박현식
# 현재 사용자가 소유한 플레이리스트 하나를 삭제한다.
@router.delete("", response_model=playlist_schema.PlaylistDeleteResponse)
def delete_playlist(
    playlist_id: int = Query(...),
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    deleted_id = playlist_crud.delete_playlist(db, current_user.id, playlist_id)
    return {"message": "플레이리스트가 삭제되었습니다.", "playlist_id": deleted_id}


# 2026.05.13 박현식
# 현재 사용자의 모든 플레이리스트를 삭제한다.
@router.delete("/all", response_model=playlist_schema.CountResponse)
def delete_all_playlists(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    count = playlist_crud.delete_all_playlists(db, current_user.id)
    return {"message": "모든 플레이리스트가 삭제되었습니다.", "count": count}
