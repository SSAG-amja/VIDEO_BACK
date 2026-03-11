# app/main.py
from fastapi import FastAPI
from app.api.v1.routers import api_router
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings

# 20260305 박현식
app = FastAPI(title="Pinlm API")

# 20260311 CORS 설정 반영
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 연결
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/")
def root():
    return {"message": "Pinlm Backend Server is Running!"}