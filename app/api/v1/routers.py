from fastapi import APIRouter
from app.api.v1.endpoints import user, login, movie_load

api_router = APIRouter()

api_router.include_router(user.router, prefix="/users", tags=["users"])
api_router.include_router(login.router, prefix="/login", tags=["login"])
api_router.include_router(movie_load.router, prefix="/movie_load", tags=["movie"])