from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# 20260305 박현식
# DB와 물리적으로 연결되는 엔진을 생성함
# pool_pre_ping=True는 연결이 끊겼는지 미리 확인하여 안전성을 높임
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# 20260305 박현식
# API 호출 시마다 독립적인 DB 세션을 생성하기 위한 설정
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)