from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import movie as movie_model
from app.models import post as post_model
from app.models import reply as reply_model
from app.models import report as report_model
from app.models import user as user_model
from app.schemas import report as report_schema


# 2026.08.22 임재준
# 게시글 신고 처리 및 중복 방지
def create_post_report(
    db: Session,
    post_id: int,
    current_user: user_model.User,
    request: report_schema.ReportCreateRequest,
) -> report_schema.ReportResponse:
    post = db.get(post_model.Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot report your own post.")

    existing = db.scalar(
        select(report_model.Report).where(
            report_model.Report.reporter_id == current_user.id,
            report_model.Report.target_type == "post",
            report_model.Report.post_id == post_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already reported this post.")

    report = report_model.Report(
        reporter_id=current_user.id,
        target_type="post",
        post_id=post_id,
        reason=request.reason.strip(),
        details=request.details.strip() if request.details else None,
    )
    db.add(report)
    db.commit()
    return report_schema.ReportResponse(message="Report submitted successfully.", report_id=report.id)


# 2026.08.22 임재준
# 댓글 신고 처리 및 중복 방지
def create_reply_report(
    db: Session,
    reply_id: int,
    current_user: user_model.User,
    request: report_schema.ReportCreateRequest,
) -> report_schema.ReportResponse:
    reply = db.get(reply_model.Reply, reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    if reply.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot report your own reply.")

    existing = db.scalar(
        select(report_model.Report).where(
            report_model.Report.reporter_id == current_user.id,
            report_model.Report.target_type == "reply",
            report_model.Report.reply_id == reply_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already reported this reply.")

    report = report_model.Report(
        reporter_id=current_user.id,
        target_type="reply",
        reply_id=reply_id,
        post_id=reply.post_id,
        reason=request.reason.strip(),
        details=request.details.strip() if request.details else None,
    )
    db.add(report)
    db.commit()
    return report_schema.ReportResponse(message="Report submitted successfully.", report_id=report.id)


# 2026.08.22 임재준
# 영화 메타데이터 신고 처리 및 중복 방지 (movie_id = tmdb_id 매핑 대응)
def create_movie_report(
    db: Session,
    movie_id: int,
    current_user: user_model.User,
    request: report_schema.ReportCreateRequest,
) -> report_schema.ReportResponse:
    movie = db.scalar(
        select(movie_model.Movie).where(
            (movie_model.Movie.id == movie_id) | (movie_model.Movie.tmdb_id == movie_id)
        )
    )
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    existing = db.scalar(
        select(report_model.Report).where(
            report_model.Report.reporter_id == current_user.id,
            report_model.Report.target_type == "movie",
            report_model.Report.movie_id == movie.id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already reported this movie.")

    report = report_model.Report(
        reporter_id=current_user.id,
        target_type="movie",
        movie_id=movie.id,
        reason=request.reason.strip(),
        details=request.details.strip() if request.details else None,
    )
    db.add(report)
    db.commit()
    return report_schema.ReportResponse(message="Report submitted successfully.", report_id=report.id)