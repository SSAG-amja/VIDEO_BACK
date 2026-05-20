# app/main.py
from fastapi import FastAPI
from app.api.v1.routers import api_router

# 20260305 박현식
app = FastAPI(title="Pinlm API")

# 기존에 있던 아래 줄을 삭제하세요. (deps.py에서 관리함)
# reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/login/access-token")

# 라우터 연결
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Pinlm Backend Server is Running!"}