# app/api/v1/routers.py
from fastapi import APIRouter
from app.api.v1.endpoints import user, login

api_router = APIRouter()

# tags를 ["users"]로 통일하면 Swagger에서 한 그룹으로 묶입니다.
api_router.include_router(user.router, prefix="/users", tags=["users"])
api_router.include_router(login.router, prefix="/login", tags=["login"])