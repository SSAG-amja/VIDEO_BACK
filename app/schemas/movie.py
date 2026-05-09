# 영화, 장르, ott, 배우 등과 관련된 Pydantic 모델 정의
from pydantic import BaseModel, ConfigDict, Field
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
    movie_id: int = Field(validation_alias="id")
    movie_title: str = Field(validation_alias="title_ko")
    poster_path: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class recommendedMovie(BaseModel):
    data: list[Movie]

class MovieSearchResponse(recommendedMovie):
    total_results: int