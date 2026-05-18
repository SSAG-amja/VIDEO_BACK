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


class ReplySummary(BaseModel):
    nickname: str | None
    reply_id: int
    reply_content: str
    reply_elapsed_time: int
    reply_is_mine: bool


class ReplyRequest(BaseModel):
    reply_content: str


class ReplyMutationResponse(BaseModel):
    message: str
    data: ReplySummary


class ReplyDeleteResponse(BaseModel):
    message: str
    reply_id: int


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
