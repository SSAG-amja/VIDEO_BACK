import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.core.config import settings
from app.db.async_session import AsyncSessionLocal
from app.jobs.video_updater.synchronizers.keyword_sync import KeywordSynchronizer
from app.jobs.video_updater.synchronizers.meta_sync import MetaSynchronizer
from app.jobs.video_updater.synchronizers.movie_sync import MovieSynchronizer
from app.jobs.video_updater.synchronizers.person_sync import PersonSynchronizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("VIDEO_UPDATER_MAIN")
PIPELINE_LOCK_KEY = 20260510


# 26.05.17 김광원
# TMDB 기준 UTC 날짜로 dump와 change API 조회 범위를 계산한다.
def get_target_dates():
    now_utc = datetime.now(timezone.utc)
    dump_date_str = now_utc.strftime("%m_%d_%Y")
    start_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = now_utc.strftime("%Y-%m-%d")
    return dump_date_str, start_date, end_date


# 26.05.17 김광원
# 메타/키워드/인물/영화 동기화를 하나의 트랜잭션으로 실행한다.
async def run_pipeline():
    logger.info("Pinlm VIDEO_UPDATER 파이프라인 가동 시작")

    dump_date_str, start_date, end_date = get_target_dates()
    logger.info("타겟 날짜 | Dump: %s, Change API: %s ~ %s", dump_date_str, start_date, end_date)

    async with aiohttp.ClientSession() as aio_session:
        async with AsyncSessionLocal() as db_session:
            lock_acquired = False
            try:
                lock_acquired = bool(
                    await db_session.scalar(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": PIPELINE_LOCK_KEY},
                    )
                )
                if not lock_acquired:
                    logger.warning("이미 다른 VIDEO_UPDATER 실행이 같은 DB에서 동작 중이므로 이번 실행은 건너뜁니다.")
                    await db_session.rollback()
                    return

                meta_sync = MetaSynchronizer(db_session)
                await meta_sync.sync_genres(aio_session)
                await meta_sync.sync_otts(aio_session)

                keyword_sync = KeywordSynchronizer(db_session)
                await keyword_sync.sync_keywords(dump_date_str)

                person_sync = PersonSynchronizer(db_session)
                await person_sync.sync_people(aio_session, dump_date_str, start_date, end_date)

                movie_sync = MovieSynchronizer(db_session)
                await movie_sync.sync_movies(aio_session, dump_date_str, start_date, end_date)

                await db_session.commit()
            except Exception as exc:
                logger.error("파이프라인 실행 중 치명적 오류 발생: %s", str(exc), exc_info=True)
                await db_session.rollback()
                raise
            finally:
                if lock_acquired:
                    await release_pipeline_lock(db_session)

    logger.info("Pinlm VIDEO_UPDATER 파이프라인이 안전하게 종료되었습니다.")


# 26.05.17 김광원
# PostgreSQL advisory lock을 해제한다.
async def release_pipeline_lock(db_session):
    try:
        await db_session.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": PIPELINE_LOCK_KEY},
        )
    except Exception:
        logger.warning("advisory lock 해제 중 예외가 발생했습니다.", exc_info=True)


# 26.05.17 김광원
# APScheduler에서 파이프라인을 안전하게 호출한다.
async def run_pipeline_job():
    try:
        await run_pipeline()
    except Exception:
        logger.exception("스케줄 작업 실행 실패")


# 26.05.17 김광원
# 하루 1회 updater 파이프라인을 실행하는 스케줄러를 유지한다.
async def run_scheduler_forever():
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.SCHEDULER_TIMEZONE))
    scheduler.add_job(
        run_pipeline_job,
        trigger=CronTrigger(hour=settings.SCHEDULER_HOUR, minute=settings.SCHEDULER_MINUTE),
        id="daily_video_updater",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "APScheduler 시작 | timezone=%s, daily=%02d:%02d, run_on_startup=%s, exit_after_startup_run=%s",
        settings.SCHEDULER_TIMEZONE,
        settings.SCHEDULER_HOUR,
        settings.SCHEDULER_MINUTE,
        settings.RUN_ON_STARTUP,
        settings.EXIT_AFTER_STARTUP_RUN,
    )

    if settings.RUN_ON_STARTUP:
        await run_pipeline_job()
        if settings.EXIT_AFTER_STARTUP_RUN:
            logger.info("1회 실행 검증 모드이므로 startup 파이프라인 종료 후 프로세스를 종료합니다.")
            return

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        asyncio.run(run_scheduler_forever())
    except KeyboardInterrupt:
        logger.info("스케줄러 종료 신호를 받았습니다.")
    except Exception:
        raise SystemExit(1)
