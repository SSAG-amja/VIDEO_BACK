from pydantic import BaseModel


class PassedMovieSummary(BaseModel):
    movie_id: int | None
    movie_title: str | None
    poster_path: str | None = None


class PassedMovieListResponse(BaseModel):
    total: int
    data: list[PassedMovieSummary]


class PassedMovieMutationResponse(BaseModel):
    message: str
    data: PassedMovieSummary


class PassedCountResponse(BaseModel):
    message: str
    count: int
