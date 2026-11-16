from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import report as report_crud
from app.models import user as user_model
from app.schemas import report as report_schema

router = APIRouter()


# 2026.08.22 임재준
# 게시글 신고 엔드포인트
@router.post("/posts/{post_id}", response_model=report_schema.ReportResponse, tags=["Report"])
def report_post(
    post_id: int,
    request: report_schema.ReportCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return report_crud.create_post_report(db, post_id, current_user, request)


# 2026.08.22 임재준
# 댓글 신고 엔드포인트
@router.post("/replies/{reply_id}", response_model=report_schema.ReportResponse, tags=["Report"])
def report_reply(
    reply_id: int,
    request: report_schema.ReportCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return report_crud.create_reply_report(db, reply_id, current_user, request)


# 2026.08.22 임재준
# 영화 정보 신고 엔드포인트
@router.post("/movies/{movie_id}", response_model=report_schema.ReportResponse, tags=["Report"])
def report_movie(
    movie_id: int,
    request: report_schema.ReportCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return report_crud.create_movie_report(db, movie_id, current_user, request)