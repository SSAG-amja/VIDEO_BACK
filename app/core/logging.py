import logging
from logging.config import dictConfig
from pathlib import Path


# 2026.06.04 김호영
# 추천 worker/scheduler 실행 시 콘솔과 파일 로그를 함께 남기도록 설정한다.
def configure_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                "file": {
                    "class": "logging.FileHandler",
                    "filename": "logs/app.log",
                    "formatter": "default",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["console", "file"],
            },
        }
    )
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
