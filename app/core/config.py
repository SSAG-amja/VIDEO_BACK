# app/core/config.py
import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "Pinlm_Backend"
    
    # 20260305 박현식: 자물쇠(Authorize) 경로 에러 해결을 위해 추가
    API_V1_STR: str = "/api/v1"
    
    # .env의 개별 항목들을 조합하여 DATABASE_URL 생성
    DB_USER: str = os.getenv("DB_USER")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD")
    DB_HOST: str = os.getenv("DB_HOST")
    DB_PORT: str = os.getenv("DB_PORT")
    DB_NAME: str = os.getenv("DB_NAME")
    
    # SQLAlchemy용 드라이버(+psycopg2)를 포함한 URL 조합
    DATABASE_URL: str = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    TOKEN_EXP_TIME: int = int(os.getenv("TOKEN_EXP_TIME", 30))

    # 20260305 박현식: SERVER_PORT 등 정의되지 않은 .env 변수가 있어도 에러 무시
    model_config = SettingsConfigDict(
        extra="ignore"
    )

settings = Settings()