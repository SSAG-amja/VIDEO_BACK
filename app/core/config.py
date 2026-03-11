import os
import socket
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

# 260311 김호영 - 개발 환경에서 현재 노트북의 IP를 자동으로 찾아내어 CORS 허용 리스트에 추가하는 기능 구현
# 현재 와이파이의 내부 IP를 자동으로 찾아내는 함수
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class Settings(BaseSettings):
    PROJECT_NAME: str = "Pinlm_Backend"
    API_V1_STR: str = "/api/v1"
    
    # 개발 환경인지 배포 환경인지 구분하는 변수 추가 (기본값은 development)
    ENVIRONMENT: str = "development" 
    
    # DB 설정
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: str
    DB_NAME: str
    
    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    TOKEN_EXP_TIME: int = 30
    TMDB_API_KEY: str

    # CORS 설정 추가 (.env에서 고정으로 허용할 주소들만 받음)
    CORS_ORIGINS: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        # 1. .env에 적힌 기본 주소들을 파싱(production 환경에서는 이 주소들만 허용)
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        
        # 2. 개발(development) 환경일 경우, 현재 노트북의 IP를 자동으로 CORS 리스트에 추가!
        if self.ENVIRONMENT == "development":
            local_ip = get_local_ip()
            dynamic_origins = [
                f"http://localhost:8081",
                f"http://127.0.0.1:8081",
                f"http://{local_ip}:8081",  # Expo 프론트엔드 포트
                f"http://{local_ip}:8000",  # 백엔드 자신
            ]
            # 기존 origins와 합치고 중복 제거
            origins = list(set(origins + dynamic_origins))
            
        return origins

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

settings = Settings()