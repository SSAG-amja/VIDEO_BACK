# 영화, 장르, ott, 배우 등과 관련된 Pydantic 모델 정의
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional

class Genre(BaseModel):
    genre_id: int = Field(validation_alias="id")
    genre_name: str = Field(validation_alias="name_ko")

    model_config = ConfigDict(from_attributes=True)

class OTT(BaseModel):
    ott_id: int = Field(validation_alias="id")
    ott_name: str = Field(validation_alias="name_ko") 
    
    model_config = ConfigDict(from_attributes=True)

class Movie(BaseModel):
    movie_id: int
    movie_title: str
    poster_path: Optional[str] = None