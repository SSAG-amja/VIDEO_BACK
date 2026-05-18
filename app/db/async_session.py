from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings


# 26.05.17 김광원
# 동기 DB URL을 asyncpg 드라이버 URL로 변환한다.
def get_async_database_url() -> str:
    database_url = settings.DATABASE_URL

    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


async_engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=async_engine,
)


# 26.05.17 김광원
# 비동기 작업용 DB 세션을 생성하고 종료한다.
async def get_async_db():
    async with AsyncSessionLocal() as db:
        yield db
