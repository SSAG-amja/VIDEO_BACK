# app/main.py
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.api.v1.routers import api_router
from app.core.config import settings
from app.db.session import SessionLocal
# from app.db.init_data import MovieDataSeeder

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 260410 김광원
# 서버 시작시 init_data.py 실행
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     os.system("alembic upgrade head")
    
#     db = SessionLocal()
#     try:
#         csv_path = os.path.join(BASE_DIR, "data", "movie.csv")
#         MovieDataSeeder(db, csv_path).execute()
        
#     except Exception as e:
#         logging.error(f"Error during data seeding: {e}")
#     finally:
#         db.close()
    
#     yield

app = FastAPI(title="PINLM API") #, lifespan=lifespan)

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