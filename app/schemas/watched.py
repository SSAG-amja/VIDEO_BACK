from pydantic import BaseModel


class WatchedMovieSummary(BaseModel):
    movie_id: int | None
    movie_title: str | None
    poster_path: str | None = None


class WatchedMovieListResponse(BaseModel):
    total: int
    data: list[WatchedMovieSummary]


class WatchedMovieMutationResponse(BaseModel):
    message: str
    data: WatchedMovieSummary


class WatchedCountResponse(BaseModel):
    message: str
    count: int
