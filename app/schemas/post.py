from pydantic import BaseModel, Field, model_validator


class PostCreateRequest(BaseModel):
    is_playlist: bool = False
    movie_id: int | None = None
    playlist_id: int | None = None
    post_title: str
    post_content: str
    hashtags: list[str] = Field(default_factory=list)

    # 2026.05.18 박현식
    # 게시물 생성 시 영화 공유와 플레이리스트 공유에 필요한 대상 id가 있는지 검증한다.
    @model_validator(mode="after")
    def validate_target(self):
        if self.is_playlist and self.playlist_id is None:
            raise ValueError("playlist_id is required when is_playlist is true")
        if not self.is_playlist and self.movie_id is None:
            raise ValueError("movie_id is required when is_playlist is false")
        return self


class PostUpdateRequest(BaseModel):
    post_title: str | None = None
    post_content: str | None = None
    hashtags: list[str] | None = None

    # 2026.05.18 박현식
    # 게시물 수정 요청에 최소 하나 이상의 변경 필드가 포함됐는지 검증한다.
    @model_validator(mode="after")
    def validate_has_update(self):
        if self.post_title is None and self.post_content is None and self.hashtags is None:
            raise ValueError("At least one field is required.")
        return self


class GenreSummary(BaseModel):
    genre_id: int | None
    name: str | None


class ActorSummary(BaseModel):
    actor_name: str | None
    actor_profile: str | None = None


class OttSummary(BaseModel):
    ott_id: int | None
    ott_name: str | None
    type: str


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 대댓글 부모 ID, 멘션 대상, 댓글 좋아요 필드 추가
class ReplySummary(BaseModel):
    nickname: str | None
    reply_id: int
    reply_content: str
    reply_elapsed_time: int
    reply_is_mine: bool
    parent_id: int | None = None
    reply_to_user: str | None = None
    reply_likes: int = 0
    reply_is_liked: bool = False


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 대댓글 등록을 위한 parent_id 옵셔널 필드 추가
class ReplyRequest(BaseModel):
    reply_content: str
    parent_id: int | None = None


class ReplyMutationResponse(BaseModel):
    message: str
    data: ReplySummary


class ReplyDeleteResponse(BaseModel):
    message: str
    reply_id: int


# 2026.08.14 임재준
# 댓글 좋아요 및 좋아요 취소 결과에 필요한 응답 스키마를 정의한다.
class ReplyLikeResponse(BaseModel):
    message: str
    reply_id: int
    reply_likes: int
    reply_is_liked: bool


class PostLikeResponse(BaseModel):
    message: str
    post_id: int
    post_likes: int
    post_is_liked: bool


class PostPlaylistMovieSummary(BaseModel):
    movie_id: int | None
    movie_title: str | None = None
    poster_path: str | None = None


class PostResponse(BaseModel):
    post_id: int
    post_elapsed_time: int
    posting_time: int
    is_playlist: bool
    nickname: str | None
    movie_id: int | None = None
    movie_title: str | None = None
    poster_path: str | None = None
    director: str | None = None
    genres: list[GenreSummary] = Field(default_factory=list)
    actors: list[ActorSummary] = Field(default_factory=list)
    otts: list[OttSummary] = Field(default_factory=list)
    playlist_id: int | None = None
    playlist_title: str | None = None
    movies: list[PostPlaylistMovieSummary] = Field(default_factory=list)
    post_title: str
    post_content: str
    hashtags: list[str]
    post_likes: int
    post_replies: int
    post_is_mine: bool = False
    post_is_liked: bool = False
    replies: list[ReplySummary] = Field(default_factory=list)


class PostListResponse(BaseModel):
    data: list[PostResponse]


class PostMutationResponse(BaseModel):
    message: str
    data: PostResponse | None = None


class PostDeleteResponse(BaseModel):
    message: str
    post_id: int