from collections.abc import Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models.mapping import UserInteraction
from app.models.movie import Movie
from app.models.recommendations import Recommendation
from app.models.user import User


# 2026.06.04 김호영
# 사용자별 사전 계산 추천 후보를 rank 순서로 조회한다.
def load_precomputed_candidates(db: Session, user_id: int, limit: int) -> list[tuple[Recommendation, Movie]]:
    stmt = (
        select(Recommendation, Movie)
        .join(Movie, Movie.id == Recommendation.movie_id)
        .where(Recommendation.user_id == user_id)
        .order_by(Recommendation.rank)
        .limit(limit)
    )
    return list(db.execute(stmt).all())


# 2026.06.04 김호영
# 특정 사용자의 기존 추천 후보를 새 추천 후보 목록으로 교체한다.
def replace_user_recommendation_rows(
    db: Session,
    user_id: int,
    recommendation_rows: list[Recommendation],
) -> None:
    db.query(Recommendation).filter(Recommendation.user_id == user_id).delete()
    db.add_all(recommendation_rows)


def load_eligible_users_and_exclusions(
    db: Session,
    artifact_user_ids: Sequence[int],
    *,
    chunk_size: int = 1_000,
) -> tuple[tuple[int, ...], dict[int, set[int]]]:
    requested = tuple(int(value) for value in artifact_user_ids)
    eligible: set[int] = set()
    exclusions: dict[int, set[int]] = {}
    for start in range(0, len(requested), chunk_size):
        user_chunk = requested[start : start + chunk_size]
        eligible.update(
            int(value)
            for value in db.execute(
                select(User.id).where(User.id.in_(user_chunk), User.deleted_at.is_(None))
            ).scalars()
        )
        rows = db.execute(
            select(UserInteraction.user_id, UserInteraction.movie_id).where(
                UserInteraction.user_id.in_(user_chunk),
                (
                    UserInteraction.is_watched.is_(True)
                    | UserInteraction.is_passed.is_(True)
                ),
            )
        )
        for user_id, movie_id in rows:
            exclusions.setdefault(int(user_id), set()).add(int(movie_id))
    ordered_eligible = tuple(user_id for user_id in requested if user_id in eligible)
    return ordered_eligible, exclusions


def replace_precomputed_candidate_rows(
    db: Session,
    *,
    user_ids: Sequence[int],
    rows: list[dict],
    statement_chunk_size: int = 5_000,
) -> None:
    normalized_user_ids = [int(value) for value in user_ids]
    if normalized_user_ids:
        db.execute(
            delete(Recommendation).where(Recommendation.user_id.in_(normalized_user_ids))
        )
    for start in range(0, len(rows), statement_chunk_size):
        db.execute(insert(Recommendation), rows[start : start + statement_chunk_size])


def load_v3_candidate_rows(
    db: Session,
    *,
    user_id: int,
    limit: int,
) -> list[Recommendation]:
    if limit <= 0:
        return []
    stmt = (
        select(Recommendation)
        .where(
            Recommendation.user_id == user_id,
            Recommendation.source.in_(("lightfm_v3", "lightfm_v3_feature_only")),
        )
        .order_by(Recommendation.rank)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def load_v3_candidate_publication_summary(
    db: Session,
    *,
    model_build_id: str,
    candidate_snapshot_id: str,
) -> tuple[int, int]:
    stmt = select(
        func.count(Recommendation.movie_id),
        func.count(func.distinct(Recommendation.user_id)),
    ).where(
        Recommendation.source == "lightfm_v3",
        Recommendation.source_scores["model_build_id"].as_string() == model_build_id,
        Recommendation.source_scores["candidate_snapshot_id"].as_string()
        == candidate_snapshot_id,
    )
    row = db.execute(stmt).one()
    return int(row[0] or 0), int(row[1] or 0)
