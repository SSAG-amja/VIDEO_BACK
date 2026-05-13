from pydantic import BaseModel


class MovieSummary(BaseModel):
    movie_id: int | None
    movie_title: str | None
    poster_path: str | None = None


class MovieListResponse(BaseModel):
    total: int
    data: list[MovieSummary]


class PlaylistCreateRequest(BaseModel):
    playlist_title: str
    playlist_is_public: bool = False


class PlaylistUpdateRequest(BaseModel):
    playlist_id: int
    playlist_title: str | None = None
    playlist_is_public: bool | None = None


class PlaylistMovieRequest(BaseModel):
    movie_id: int


class PlaylistSummary(BaseModel):
    playlist_id: int
    playlist_title: str
    playlist_is_public: bool
    movie_count: int = 0
    movies: list[MovieSummary] = []


class PlaylistListResponse(BaseModel):
    total: int
    data: list[PlaylistSummary]


class PlaylistMutationResponse(BaseModel):
    message: str
    data: PlaylistSummary


class PlaylistDeleteResponse(BaseModel):
    message: str
    playlist_id: int


class CountResponse(BaseModel):
    message: str
    count: int


class MovieMutationResponse(BaseModel):
    message: str
    data: MovieSummary
