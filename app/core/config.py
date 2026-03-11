import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

# 20260311 박현식: .env 기반 CORS 및 환경 설정 최적화
class Settings(BaseSettings):
    PROJECT_NAME: str = "Pinlm_Backend"
    API_V1_STR: str = "/api/v1"
    
    # DB 설정 (Pydantic이 .env에서 자동으로 매칭하여 읽어옵니다)
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

    # CORS 설정 추가
    CORS_ORIGINS: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        if not self.CORS_ORIGINS:
            return []
        # 쉼표로 구분된 문자열을 리스트로 변환
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",  # .env 파일을 자동으로 읽도록 설정
        extra="ignore"
    )

settings = Settings()