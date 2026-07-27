from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recommendation_runs import RecommendationRun


def create_run(
    db: Session,
    *,
    run_id: str,
    engine: str,
    engine_version: str,
    run_type: str,
    config_snapshot: dict,
    ontology_build_id: int | None = None,
) -> RecommendationRun:
    run = RecommendationRun(
        run_id=run_id,
        engine=engine,
        engine_version=engine_version,
        ontology_build_id=ontology_build_id,
        run_type=run_type,
        config_snapshot=config_snapshot,
        status="running",
    )
    db.add(run)
    db.flush()
    return run


def mark_run_finished(
    db: Session,
    run: RecommendationRun,
    *,
    status: str,
    processed_user_count: int = 0,
    generated_candidate_count: int = 0,
    source_counts: dict | None = None,
    fallback_ratio: float | None = None,
    failure_reason: str | None = None,
) -> RecommendationRun:
    now = datetime.utcnow()
    run.status = status
    run.finished_at = now
    if run.started_at:
        run.elapsed_time = (now - run.started_at).total_seconds()
    run.processed_user_count = processed_user_count
    run.generated_candidate_count = generated_candidate_count
    run.source_counts = source_counts
    run.fallback_ratio = fallback_ratio
    run.failure_reason = failure_reason
    db.flush()
    return run


def get_run(db: Session, run_id: str) -> RecommendationRun | None:
    return db.execute(select(RecommendationRun).where(RecommendationRun.run_id == run_id)).scalar_one_or_none()
