from pydantic import BaseModel


# 2026.08.22 임재준
# 게시글, 댓글, 영화 통합 신고 요청 스키마
class ReportCreateRequest(BaseModel):
    reason: str
    details: str | None = None


class ReportResponse(BaseModel):
    message: str
    report_id: int