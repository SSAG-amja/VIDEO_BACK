# 1. 부모 테이블 모델들
from .user import Users
from .movie import Movies
from .genre import Genres
from .ott import OTTs

# 2. 매핑(중간) 테이블 모델들
from .mapping import (
    User_Genres, 
    User_Otts, 
    User_Favorite_Movies, 
    Movie_Genres, 
    Movie_Otts
)