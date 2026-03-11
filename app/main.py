# app/main.py
from fastapi import FastAPI
from app.api.v1.routers import api_router
from fastapi.middleware.cors import CORSMiddleware

# 20260305 박현식
app = FastAPI(title="Pinlm API")

# 라우터 연결
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Pinlm Backend Server is Running!"}

app.add_middleware(
    CORSMiddleware,
    # 허용할 도메인 목록
    allow_origins=[
        "http://localhost:8081",    # Expo Web 환경
        "http://127.0.0.1:8081",
        "http://192.168.0.95:8081",    # 실제 로컬 IP로 변경:8081
        "http://192.168.0.95:8000"     # 실제 로컬 IP로 변경:8000
    ],
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메서드(GET, POST 등) 허용
    allow_headers=["*"],  # 모든 헤더 허용
)