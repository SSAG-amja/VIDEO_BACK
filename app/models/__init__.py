# models/__init__.py
from .user import User
from .people import People
from .keyword import Keyword
from .hashtag import Hashtag
from .post import Post
from .reply import Reply
from .playlist import Playlist
from .movie import Movie
from .ott import Ott
from .genre import Genre

from .mapping import (
    UserInteraction, PlaylistMovie, MovieOtt, MovieActor
)