import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

#260307박현식

class Settings(BaseSettings):
    PROJECT_NAME: str = "Pinlm_Backend"
    
    API_V1_STR: str = "/api/v1"
    
    DB_USER: str = os.getenv("DB_USER")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD")
    DB_HOST: str = os.getenv("DB_HOST")
    DB_PORT: str = os.getenv("DB_PORT")
    DB_NAME: str = os.getenv("DB_NAME")
    
    DATABASE_URL: str = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    TOKEN_EXP_TIME: int = int(os.getenv("TOKEN_EXP_TIME", 30))

    TMDB_API_KEY: str = os.getenv("TMDB_API_KEY")

    model_config = SettingsConfigDict(
        extra="ignore"
    )

settings = Settings()