# 영화, 장르, ott, 배우 등과 관련된 Pydantic 모델 정의
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Optional

class Genre(BaseModel):
    genre_id: int = Field(validation_alias="id")
    genre_name: str = Field(validation_alias="name_ko")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class OTT(BaseModel):
    ott_id: int = Field(validation_alias="id")
    ott_name: str = Field(validation_alias="name_ko")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class Movie(BaseModel):
    movie_id: int = Field(validation_alias=AliasChoices("tmdb_id", "id"))
    movie_title: str = Field(validation_alias="title_ko")
    poster_path: Optional[str] = None
    vote_average: Optional[float] = None
    popularity: Optional[float] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class recommendedMovie(BaseModel):
    data: list[Movie]

class MovieSearchItem(BaseModel):
    movie_id: int
    movie_title: str
    poster_path: Optional[str] = None
    vote_average: Optional[float] = None
    popularity: Optional[float] = None
    badge: Optional[str] = None

class MovieSearchResponse(BaseModel):
    total_results: int
    data: list[MovieSearchItem]
