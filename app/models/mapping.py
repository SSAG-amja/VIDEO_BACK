from sqlalchemy import Column, Integer, ForeignKey
from app.db.base import Base

class Movie_Genres(Base):
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)

class Movie_Otts(Base):
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    ott_id = Column(Integer, ForeignKey("otts.id", ondelete="CASCADE"), primary_key=True)

class User_Genres(Base):
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)

class User_Otts(Base):
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ott_id = Column(Integer, ForeignKey("otts.id", ondelete="CASCADE"), primary_key=True)

class User_Favorite_Movies(Base):
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)