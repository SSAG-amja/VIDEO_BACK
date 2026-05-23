from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import interaction as interaction_crud
from app.models import user as user_model
from app.schemas import action as action_schema
from app.services import recsys_client

router = APIRouter()

RECSYS_ACTION_BY_BACK_ACTION = {
    "pin": "pinned",
    "passed": "passed",
    "watched": "watched",
}


# 2026.05.23 김호영
# 사용자 액션 저장 후 VIDEO_RECSYS 최근 행동 캐시에 동일 액션을 전달한다.
# 2026.05.13 박현식
# 홈 피드와 상세 화면의 사용자 액션을 DB 상태로 저장한다.
@router.patch("/{movie_id}", response_model=action_schema.InteractionUpdateResponse)
def update_movie_interaction(
    movie_id: int,
    request: action_schema.InteractionUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    movie = interaction_crud.get_movie_by_tmdb_id(db, movie_id)
    response = interaction_crud.update_movie_interaction(db, current_user.id, movie_id, request)
    recsys_action = RECSYS_ACTION_BY_BACK_ACTION.get(request.action_type)
    if recsys_action:
        recsys_client.record_interaction(current_user.id, movie.id, recsys_action)
    return response
