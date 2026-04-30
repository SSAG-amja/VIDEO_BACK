# 영화, 장르, ott, 배우 등과 관련된 Pydantic 모델 정의
from pydantic import BaseModel, Field
from typing import List

class Genre(BaseModel):
    genre_id: int
    genre_name: str

class OTT(BaseModel):
    ott_id: int
    ott_name: str

class Movie(BaseModel):
    movie_id: int
    movie_title: str