import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logging import configure_logging
from app.jobs.recsys.v1.worker import run_worker


logger = logging.getLogger(__name__)


def next_run_at(now: datetime, *, hour: int, minute: int) -> datetime:
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled


# 2026.06.04 김호영
# 추천 worker를 설정된 시간에 하루 1회 실행하는 scheduler 루프를 유지한다.
def run_scheduler() -> None:
    timezone = ZoneInfo(settings.WORKER_SCHEDULE_TIMEZONE)
    logger.info(
        "worker scheduler started hour=%s minute=%s timezone=%s",
        settings.WORKER_SCHEDULE_HOUR,
        settings.WORKER_SCHEDULE_MINUTE,
        settings.WORKER_SCHEDULE_TIMEZONE,
    )

    while True:
        now = datetime.now(timezone)
        scheduled_at = next_run_at(
            now,
            hour=settings.WORKER_SCHEDULE_HOUR,
            minute=settings.WORKER_SCHEDULE_MINUTE,
        )
        sleep_seconds = max((scheduled_at - now).total_seconds(), 0)
        logger.info("next worker run scheduled_at=%s sleep_seconds=%s", scheduled_at.isoformat(), int(sleep_seconds))
        time.sleep(sleep_seconds)

        try:
            logger.info("scheduled worker run started")
            run_worker()
            logger.info("scheduled worker run finished")
        except Exception:
            logger.exception("scheduled worker run failed")


if __name__ == "__main__":
    configure_logging()
    run_scheduler()
