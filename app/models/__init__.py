# 1. 부모 테이블 모델들
from .user import User
from .actor import Actor
from .keyword import Keyword
from .hashtag import Hashtag
from .post import Post
from .reply import Reply
from .playlist import Playlist
from .movie import Movie
from .ott import Ott
from .genre import Genre

# 2. 매핑(중간) 테이블 모델들
from .mapping import (
    movie_genres,
    movie_otts,
    movie_actors,
    movie_keywords,
    user_genres,
    user_otts,
    user_favorite_movies,
    post_hashtags,
    likes
)