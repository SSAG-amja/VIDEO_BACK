from pydantic import BaseModel


class PinnedMovieSummary(BaseModel):
    movie_id: int | None
    movie_title: str | None
    poster_path: str | None = None


class PinnedMovieListResponse(BaseModel):
    total: int
    data: list[PinnedMovieSummary]


class PinnedMovieMutationResponse(BaseModel):
    message: str
    data: PinnedMovieSummary


class PinnedCountResponse(BaseModel):
    message: str
    count: int
