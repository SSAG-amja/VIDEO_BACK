import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# 20260305 박현식
# 컨테이너 내부의 /back 경로를 인식할 수 있도록 설정
sys.path.append(os.path.join(os.getcwd(), "..", "..", ".."))

from app.core.config import settings
from app.db.base import Base

config = context.config

# 20260305 박현식
# 도커 컨테이너 내부에서 .env의 DATABASE_URL을 동적으로 주입함
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

run_migrations_online()