from fastapi import APIRouter
from app.api.v1.endpoints import auth, movie_load, explore, user

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(user.router, prefix="/user", tags=["user"])
api_router.include_router(movie_load.router, prefix="/movie_load", tags=["movie"])
api_router.include_router(explore.router, prefix="/explore", tags=["Explore"])