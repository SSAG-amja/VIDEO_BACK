from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import post as post_crud
from app.models import user as user_model
from app.schemas import post as post_schema

router = APIRouter()


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 투표 데이터(poll)가 포함된 게시물 생성 요청 처리
# 게시물 생성 요청의 인증 사용자와 본문을 crud 계층으로 전달하고 생성 응답 메시지를 구성한다.
@router.post("", response_model=post_schema.PostMutationResponse, tags=["Post"])
def create_post(
    request: post_schema.PostCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    post = post_crud.create_post(db, current_user.id, request)
    return {"message": "Post created.", "data": post}


# 2026.05.18 박현식
# 현재 사용자 기준의 커뮤니티 게시물 목록 조회를 crud 계층에 위임한다.
@router.get("", response_model=post_schema.PostListResponse, tags=["Post"])
def read_posts(
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.get_posts(db, current_user.id)


# 2026.05.18 박현식
# 단일 게시물 상세 조회 요청을 받아 댓글 포함 응답을 반환한다.
@router.get("/{post_id}", response_model=post_schema.PostResponse, tags=["Post"])
def read_post(
    post_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.get_post(db, post_id, current_user.id)


# 2026.05.18 박현식
# 내 게시물 수정 요청을 crud 계층으로 전달하고 수정 응답 메시지를 구성한다.
@router.patch("/{post_id}", response_model=post_schema.PostMutationResponse, tags=["Post"])
def update_post(
    post_id: int,
    request: post_schema.PostUpdateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    post = post_crud.update_post(db, post_id, current_user, request)
    return {"message": "Post updated.", "data": post}


# 2026.08.14 임재준
# 특정 게시물에 첨부된 투표에 참여하고 갱신된 투표 결과를 반환한다.
@router.post("/{post_id}/poll/vote", response_model=post_schema.PollVoteResponse, tags=["Poll"])
def vote_post_poll(
    post_id: int,
    request: post_schema.PollVoteRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.vote_poll(db, post_id, request.option_id, current_user)


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 대댓글(parent_id) 및 유저 멘션이 포함된 댓글 생성 요청 처리
# 댓글 작성 요청을 Reply 분류 API로 받고 생성된 댓글 요약을 반환한다.
@router.post("/{post_id}/replies", response_model=post_schema.ReplyMutationResponse, tags=["Reply"])
def create_reply(
    post_id: int,
    request: post_schema.ReplyRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    reply = post_crud.create_reply(db, post_id, current_user, request)
    return {"message": "Reply created.", "data": reply}


# 2026.05.18 박현식
# 댓글 수정 요청을 Reply 분류 API로 받고 수정된 댓글 요약을 반환한다.
@router.patch("/{post_id}/replies/{reply_id}", response_model=post_schema.ReplyMutationResponse, tags=["Reply"])
def update_reply(
    post_id: int,
    reply_id: int,
    request: post_schema.ReplyRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    reply = post_crud.update_reply(db, post_id, reply_id, current_user, request)
    return {"message": "Reply updated.", "data": reply}


# 2026.05.18 박현식
# 댓글 삭제 요청을 Reply 분류 API로 받고 삭제된 댓글 id를 반환한다.
@router.delete("/{post_id}/replies/{reply_id}", response_model=post_schema.ReplyDeleteResponse, tags=["Reply"])
def delete_reply(
    post_id: int,
    reply_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    deleted_id = post_crud.delete_reply(db, post_id, reply_id, current_user)
    return {"message": "Reply deleted.", "reply_id": deleted_id}


# 2026.08.14 임재준
# 댓글 좋아요 요청을 Like/Reply 분류 API로 받고 보정된 댓글 좋아요 상태를 반환한다.
@router.post("/{post_id}/replies/{reply_id}/likes", response_model=post_schema.ReplyLikeResponse, tags=["Like"])
def like_reply(
    post_id: int,
    reply_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.like_reply(db, post_id, reply_id, current_user)


# 2026.08.14 임재준
# 댓글 좋아요 취소 요청을 Like/Reply 분류 API로 받고 보정된 댓글 좋아요 상태를 반환한다.
@router.delete("/{post_id}/replies/{reply_id}/likes", response_model=post_schema.ReplyLikeResponse, tags=["Like"])
def unlike_reply(
    post_id: int,
    reply_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.unlike_reply(db, post_id, reply_id, current_user)


# 2026.05.18 박현식
# 게시물 좋아요 요청을 Like 분류 API로 받고 보정된 좋아요 상태를 반환한다.
@router.post("/{post_id}/likes", response_model=post_schema.PostLikeResponse, tags=["Like"])
def like_post(
    post_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.like_post(db, post_id, current_user)


# 2026.05.18 박현식
# 게시물 좋아요 취소 요청을 Like 분류 API로 받고 보정된 좋아요 상태를 반환한다.
@router.delete("/{post_id}/likes", response_model=post_schema.PostLikeResponse, tags=["Like"])
def unlike_post(
    post_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.unlike_post(db, post_id, current_user)


# 2026.05.18 박현식
# 내 게시물 삭제 요청을 crud 계층으로 전달하고 삭제된 게시물 id를 반환한다.
@router.delete("/{post_id}", response_model=post_schema.PostDeleteResponse, tags=["Post"])
def delete_post(
    post_id: int,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    deleted_id = post_crud.delete_post(db, post_id, current_user)
    return {"message": "Post deleted.", "post_id": deleted_id}


# 2026.08.22 임재준
# 게시글 신고 엔드포인트
@router.post("/{post_id}/report", response_model=post_schema.ReportResponse, tags=["Report"])
def report_post_endpoint(
    post_id: int,
    request: post_schema.ReportCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.report_post(db, post_id, current_user, request)


# 2026.08.22 임재준
# 댓글 신고 엔드포인트
@router.post("/{post_id}/replies/{reply_id}/report", response_model=post_schema.ReportResponse, tags=["Report"])
def report_reply_endpoint(
    post_id: int,
    reply_id: int,
    request: post_schema.ReportCreateRequest,
    current_user: user_model.User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
):
    return post_crud.report_reply(db, post_id, reply_id, current_user, request)