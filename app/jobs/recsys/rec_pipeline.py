import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging
from app.crud.recsys.movies import find_popular_movies, load_existing_movie_ids
from app.crud.recsys.recommendations import replace_user_recommendation_rows
from app.crud.recsys.users import load_worker_user_ids
from app.db.session import SessionLocal
from app.models.mapping import (
    MovieActor,
    PlaylistMovie,
    UserInteraction,
    likes,
    movie_directors,
    movie_genres,
    movie_keywords,
)
from app.models.movie import Movie
from app.models.playlist import Playlist
from app.models.post import Post
from app.models.recommendations import Recommendation
from app.models.reply import Reply

logger = logging.getLogger(__name__)

ACTION_WEIGHTS = {
    "passed": -3.0,
    "pinned": 4.0,
    "watched": 6.0,
    "playlist_add": 6.0,
    "post_write": 7.0,
    "like": 1.0,
    "reply": 1.5,
}

SOURCE_CONTENT = "content"
SOURCE_COLLABORATIVE = "collaborative"
SOURCE_EXPLORE = "explore"

CONTENT_RATIO = 0.6
COLLABORATIVE_RATIO = 0.2
EXPLORE_RATIO = 0.2

GENRE_MATCH_WEIGHT = 8.0
KEYWORD_MATCH_WEIGHT = 5.0
DIRECTOR_MATCH_WEIGHT = 6.0
ACTOR_MATCH_WEIGHT = 3.0
POPULARITY_WEIGHT = 0.05
VOTE_AVERAGE_WEIGHT = 1.0
RETRY_BACKOFF_SECONDS = (1, 2, 4)
PLAYLIST_DERIVED_SIGNAL_MULTIPLIER = 0.5
RECENCY_DECAY_30_DAYS = 1.0
RECENCY_DECAY_90_DAYS = 0.8
RECENCY_DECAY_180_DAYS = 0.6
RECENCY_DECAY_OLDER = 0.4
WORKER_ADVISORY_LOCK_KEY = 2605210600


@dataclass(frozen=True)
class CandidateScore:
    movie_id: int
    score: float
    source: str
    source_scores: dict[str, float] | None = None


@dataclass(frozen=True)
class InteractionSignal:
    movie_id: int
    score: float
    exclude_from_feed: bool


@dataclass(frozen=True)
class PreferenceProfile:
    excluded_movie_ids: set[int]
    genre_scores: dict[int, float]
    keyword_scores: dict[int, float]
    director_scores: dict[int, float]
    actor_scores: dict[int, float]


@dataclass(frozen=True)
class BatchContext:
    user_ids: list[int]
    user_vectors: dict[int, dict[int, float]]


@dataclass(frozen=True)
class UserProcessResult:
    user_id: int
    status: str
    reason: str | None
    candidate_count: int
    source_counts: dict[str, int]
    source_score_totals: dict[str, float]


class CandidateValidationError(ValueError):
    pass


# 2026.06.04 김호영
# DB advisory lock으로 중복 실행을 막고 사용자별 추천 후보를 재계산한다.
def run_pipeline() -> None:
    with SessionLocal() as lock_db:
        if not acquire_worker_lock(lock_db):
            logger.warning("recommendation pipeline skipped reason=worker_already_running")
            return
        try:
            _run_pipeline_locked()
        finally:
            release_worker_lock(lock_db)


def _run_pipeline_locked() -> None:
    with SessionLocal() as db:
        batch_context = build_batch_context(db)
        logger.info(
            "recommendation pipeline started users=%s vectors=%s",
            len(batch_context.user_ids),
            len(batch_context.user_vectors),
        )
    results = [process_user(user_id, batch_context) for user_id in batch_context.user_ids]
    log_pipeline_summary(batch_context, results)


def acquire_worker_lock(db: Session) -> bool:
    return bool(db.scalar(text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": WORKER_ADVISORY_LOCK_KEY}))


def release_worker_lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": WORKER_ADVISORY_LOCK_KEY})


def build_batch_context(db: Session) -> BatchContext:
    user_ids = load_worker_user_ids(db)
    user_vectors = {
        user_id: build_user_score_vector(load_interaction_signals(db, user_id))
        for user_id in user_ids
    }
    return BatchContext(
        user_ids=user_ids,
        user_vectors={user_id: vector for user_id, vector in user_vectors.items() if vector},
    )


def process_user(user_id: int, batch_context: BatchContext | None = None) -> UserProcessResult:
    for attempt in range(1, settings.WORKER_RETRY_ATTEMPTS + 1):
        try:
            with SessionLocal() as read_db:
                candidates = generate_user_candidates(
                    read_db,
                    user_id,
                    settings.RECOMMENDATION_POOL_SIZE,
                    batch_context=batch_context,
                )
                validate_candidates(
                    read_db,
                    candidates,
                    min_count=settings.WORKER_MIN_CANDIDATE_COUNT,
                )
                recommendation_rows = build_recommendation_rows(user_id, candidates)

            with SessionLocal.begin() as write_db:
                replace_user_recommendations(write_db, user_id, recommendation_rows)

            logger.info("recommendations replaced user_id=%s candidates=%s", user_id, len(candidates))
            return build_user_process_result(user_id, "replaced", None, candidates)
        except CandidateValidationError as exc:
            logger.warning("recommendations kept user_id=%s reason=%s", user_id, exc)
            return build_user_process_result(user_id, "kept", str(exc), [])
        except OperationalError:
            if attempt >= settings.WORKER_RETRY_ATTEMPTS:
                logger.exception("recommendation replacement failed user_id=%s attempts=%s", user_id, attempt)
                return build_user_process_result(user_id, "failed", "operational_error", [])

            delay = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning("transient recommendation failure user_id=%s attempt=%s retry_in_seconds=%s", user_id, attempt, delay)
            time.sleep(delay)
        except Exception:
            logger.exception("recommendation replacement failed user_id=%s", user_id)
            return build_user_process_result(user_id, "failed", "unexpected_error", [])

    return build_user_process_result(user_id, "failed", "unknown", [])


def validate_candidates(db: Session, candidates: list[CandidateScore], *, min_count: int) -> None:
    if len(candidates) < min_count:
        raise CandidateValidationError(f"candidate_count_below_minimum count={len(candidates)} minimum={min_count}")

    movie_ids = [candidate.movie_id for candidate in candidates]
    if len(movie_ids) != len(set(movie_ids)):
        raise CandidateValidationError("duplicate_movie_id")

    invalid_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate.movie_id <= 0
            or not math.isfinite(candidate.score)
            or candidate.source not in {SOURCE_CONTENT, SOURCE_COLLABORATIVE, SOURCE_EXPLORE}
        ),
        None,
    )
    if invalid_candidate is not None:
        raise CandidateValidationError(f"invalid_candidate movie_id={invalid_candidate.movie_id}")

    existing_movie_ids = load_existing_movie_ids(db, movie_ids)
    missing_movie_ids = set(movie_ids) - existing_movie_ids
    if missing_movie_ids:
        raise CandidateValidationError(f"missing_movie_ids count={len(missing_movie_ids)}")


def generate_user_candidates(
    db: Session,
    user_id: int,
    pool_size: int,
    *,
    batch_context: BatchContext | None = None,
) -> list[CandidateScore]:
    signals = load_interaction_signals(db, user_id)
    profile = build_preference_profile(db, signals)
    content_count, collaborative_count, explore_count = allocate_candidate_counts(pool_size)

    content_candidates = build_content_candidates(db, profile, limit=content_count * 3)
    collaborative_candidates = build_collaborative_candidates(
        db,
        user_id,
        profile,
        limit=collaborative_count * 3,
        batch_context=batch_context,
    )
    ranked_candidates = select_source_quotas(
        {
            SOURCE_CONTENT: content_candidates,
            SOURCE_COLLABORATIVE: collaborative_candidates,
        },
        {
            SOURCE_CONTENT: content_count,
            SOURCE_COLLABORATIVE: collaborative_count,
        },
        excluded_movie_ids=profile.excluded_movie_ids,
    )

    required_explore_count = max(explore_count, pool_size - len(ranked_candidates))
    explore_candidates = build_exploration_candidates(
        db,
        excluded_movie_ids=profile.excluded_movie_ids | {candidate.movie_id for candidate in ranked_candidates},
        limit=required_explore_count,
    )
    return [*ranked_candidates, *explore_candidates][:pool_size]


def load_interaction_signals(db: Session, user_id: int) -> list[InteractionSignal]:
    primary_signals = load_primary_interaction_signals(db, user_id)
    return [*primary_signals, *load_future_engagement_signals(db, user_id)]


def load_primary_interaction_signals(db: Session, user_id: int) -> list[InteractionSignal]:
    stmt = select(UserInteraction).where(UserInteraction.user_id == user_id)
    return [
        InteractionSignal(
            movie_id=interaction.movie_id,
            score=_interaction_weight(
                is_pinned=interaction.is_pinned,
                is_watched=interaction.is_watched,
                is_passed=interaction.is_passed,
                pinned_at=interaction.pinned_at,
                watched_at=interaction.watched_at,
                passed_at=interaction.passed_at,
            ),
            exclude_from_feed=interaction.is_watched or interaction.is_passed,
        )
        for interaction in db.scalars(stmt)
    ]


def load_future_engagement_signals(db: Session, user_id: int) -> list[InteractionSignal]:
    weighted_units: dict[tuple[str, int], float] = {}
    playlist_sizes = _playlist_sizes(db)

    _accumulate_units(
        weighted_units,
        action="playlist_add",
        rows=db.execute(
            select(PlaylistMovie.movie_id, func.count())
            .join(Playlist, Playlist.id == PlaylistMovie.playlist_id)
            .where(Playlist.user_id == user_id)
            .group_by(PlaylistMovie.movie_id)
        ),
    )
    _accumulate_units(
        weighted_units,
        action="movie_post_write",
        rows=db.execute(
            select(Post.movie_id, func.count())
            .where(Post.user_id == user_id)
            .where(Post.is_playlist.is_(False))
            .where(Post.movie_id.is_not(None))
            .group_by(Post.movie_id)
        ),
    )
    _accumulate_playlist_units(
        weighted_units,
        action="playlist_post_write",
        playlist_sizes=playlist_sizes,
        rows=db.execute(
            select(PlaylistMovie.movie_id, PlaylistMovie.playlist_id)
            .join(Post, Post.playlist_id == PlaylistMovie.playlist_id)
            .where(Post.user_id == user_id)
            .where(Post.is_playlist.is_(True))
        ),
    )
    _accumulate_units(
        weighted_units,
        action="movie_like",
        rows=db.execute(
            select(Post.movie_id, func.count())
            .select_from(likes)
            .join(Post, Post.id == likes.c.post_id)
            .where(likes.c.user_id == user_id)
            .where(Post.is_playlist.is_(False))
            .where(Post.movie_id.is_not(None))
            .group_by(Post.movie_id)
        ),
    )
    _accumulate_playlist_units(
        weighted_units,
        action="playlist_like",
        playlist_sizes=playlist_sizes,
        rows=db.execute(
            select(PlaylistMovie.movie_id, PlaylistMovie.playlist_id)
            .select_from(likes)
            .join(Post, Post.id == likes.c.post_id)
            .join(PlaylistMovie, PlaylistMovie.playlist_id == Post.playlist_id)
            .where(likes.c.user_id == user_id)
            .where(Post.is_playlist.is_(True))
        ),
    )
    _accumulate_units(
        weighted_units,
        action="movie_reply",
        rows=db.execute(
            select(Post.movie_id, func.count())
            .join(Reply, Reply.post_id == Post.id)
            .where(Reply.user_id == user_id)
            .where(Post.is_playlist.is_(False))
            .where(Post.movie_id.is_not(None))
            .group_by(Post.movie_id)
        ),
    )
    _accumulate_playlist_units(
        weighted_units,
        action="playlist_reply",
        playlist_sizes=playlist_sizes,
        rows=db.execute(
            select(PlaylistMovie.movie_id, PlaylistMovie.playlist_id)
            .select_from(Reply)
            .join(Post, Post.id == Reply.post_id)
            .join(PlaylistMovie, PlaylistMovie.playlist_id == Post.playlist_id)
            .where(Reply.user_id == user_id)
            .where(Post.is_playlist.is_(True))
        ),
    )

    scores_by_movie: dict[int, float] = {}
    for (action, movie_id), units in weighted_units.items():
        scores_by_movie[movie_id] = scores_by_movie.get(movie_id, 0.0) + _weighted_signal_score(action, units)

    return [
        InteractionSignal(movie_id=movie_id, score=score, exclude_from_feed=False)
        for movie_id, score in scores_by_movie.items()
    ]


def build_preference_profile(db: Session, signals: list[InteractionSignal]) -> PreferenceProfile:
    positive_scores: dict[int, float] = {}
    for signal in signals:
        if signal.score <= 0:
            continue
        positive_scores[signal.movie_id] = positive_scores.get(signal.movie_id, 0.0) + signal.score
    return PreferenceProfile(
        excluded_movie_ids={signal.movie_id for signal in signals if signal.exclude_from_feed},
        genre_scores=_feature_scores(db, movie_genres.c.movie_id, movie_genres.c.genre_id, positive_scores),
        keyword_scores=_feature_scores(db, movie_keywords.c.movie_id, movie_keywords.c.keyword_id, positive_scores),
        director_scores=_feature_scores(db, movie_directors.c.movie_id, movie_directors.c.director_id, positive_scores),
        actor_scores=_feature_scores(db, MovieActor.movie_id, MovieActor.actor_id, positive_scores),
    )


def build_content_candidates(db: Session, profile: PreferenceProfile, *, limit: int) -> list[CandidateScore]:
    scores: dict[int, CandidateScore] = {}
    _accumulate_feature_candidates(
        db,
        scores,
        mapping_source=movie_genres,
        movie_column=movie_genres.c.movie_id,
        feature_column=movie_genres.c.genre_id,
        feature_scores=profile.genre_scores,
        feature_weight=GENRE_MATCH_WEIGHT,
        excluded_movie_ids=profile.excluded_movie_ids,
        limit=limit,
    )
    _accumulate_feature_candidates(
        db,
        scores,
        mapping_source=movie_keywords,
        movie_column=movie_keywords.c.movie_id,
        feature_column=movie_keywords.c.keyword_id,
        feature_scores=profile.keyword_scores,
        feature_weight=KEYWORD_MATCH_WEIGHT,
        excluded_movie_ids=profile.excluded_movie_ids,
        limit=limit,
    )
    _accumulate_feature_candidates(
        db,
        scores,
        mapping_source=movie_directors,
        movie_column=movie_directors.c.movie_id,
        feature_column=movie_directors.c.director_id,
        feature_scores=profile.director_scores,
        feature_weight=DIRECTOR_MATCH_WEIGHT,
        excluded_movie_ids=profile.excluded_movie_ids,
        limit=limit,
    )
    _accumulate_feature_candidates(
        db,
        scores,
        mapping_source=MovieActor,
        movie_column=MovieActor.movie_id,
        feature_column=MovieActor.actor_id,
        feature_scores=profile.actor_scores,
        feature_weight=ACTOR_MATCH_WEIGHT,
        excluded_movie_ids=profile.excluded_movie_ids,
        limit=limit,
    )
    return sorted(scores.values(), key=lambda candidate: candidate.score, reverse=True)[:limit]


def build_collaborative_candidates(
    db: Session,
    user_id: int,
    profile: PreferenceProfile,
    *,
    limit: int,
    batch_context: BatchContext | None = None,
) -> list[CandidateScore]:
    if limit <= 0:
        return []

    user_vectors = batch_context.user_vectors if batch_context is not None else build_batch_context(db).user_vectors
    target_vector = user_vectors.get(user_id, {})
    if not target_vector:
        return []

    scores_by_movie: dict[int, float] = {}
    for other_user_id, other_vector in user_vectors.items():
        if other_user_id == user_id:
            continue

        similarity = cosine_similarity(target_vector, other_vector)
        if similarity <= 0:
            continue

        for movie_id, score in other_vector.items():
            if score <= 0 or movie_id in profile.excluded_movie_ids:
                continue
            scores_by_movie[movie_id] = scores_by_movie.get(movie_id, 0.0) + (similarity * score)

    return sorted(
        (
            CandidateScore(movie_id=movie_id, score=score, source=SOURCE_COLLABORATIVE)
            for movie_id, score in scores_by_movie.items()
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )[:limit]


def build_exploration_candidates(db: Session, *, excluded_movie_ids: set[int], limit: int) -> list[CandidateScore]:
    if limit <= 0:
        return []

    return [
        CandidateScore(movie_id=movie.id, score=movie_score(movie), source=SOURCE_EXPLORE)
        for movie in find_popular_movies(db, excluded_movie_ids=excluded_movie_ids, limit=limit)
    ]


def build_recommendation_rows(user_id: int, candidates: list[CandidateScore]) -> list[Recommendation]:
    return [
        Recommendation(
            user_id=user_id,
            movie_id=candidate.movie_id,
            score=candidate.score,
            rank=rank,
            source=candidate.source,
            source_scores=candidate.source_scores or {candidate.source: candidate.score},
        )
        for rank, candidate in enumerate(candidates, start=1)
    ]


def replace_user_recommendations(db: Session, user_id: int, recommendation_rows: list[Recommendation]) -> None:
    replace_user_recommendation_rows(db, user_id, recommendation_rows)


def allocate_candidate_counts(pool_size: int) -> tuple[int, int, int]:
    content_count = int(pool_size * CONTENT_RATIO)
    collaborative_count = int(pool_size * COLLABORATIVE_RATIO)
    explore_count = max(pool_size - content_count - collaborative_count, 0)
    return content_count, collaborative_count, explore_count


def movie_score(movie: Movie) -> float:
    return ((movie.popularity or 0.0) * POPULARITY_WEIGHT) + ((movie.vote_average or 0.0) * VOTE_AVERAGE_WEIGHT)


def select_source_quotas(
    candidates_by_source: dict[str, list[CandidateScore]],
    quotas_by_source: dict[str, int],
    *,
    excluded_movie_ids: set[int],
) -> list[CandidateScore]:
    merged: dict[int, CandidateScore] = {}
    selected_counts = {source: 0 for source in quotas_by_source}

    for source, quota in quotas_by_source.items():
        for candidate in candidates_by_source.get(source, []):
            if candidate.movie_id in excluded_movie_ids:
                continue

            existing = merged.get(candidate.movie_id)
            if existing is None:
                if selected_counts[source] >= quota:
                    continue
                merged[candidate.movie_id] = candidate
                selected_counts[source] += 1
                continue

            merged[candidate.movie_id] = CandidateScore(
                movie_id=candidate.movie_id,
                score=existing.score + candidate.score,
                source=existing.source,
                source_scores=merge_source_scores(existing, candidate),
            )

    return sorted(merged.values(), key=lambda candidate: candidate.score, reverse=True)


def _feature_scores(db: Session, movie_column, feature_column, movie_scores: dict[int, float]) -> dict[int, float]:
    if not movie_scores:
        return {}

    stmt = select(movie_column, feature_column).where(movie_column.in_(movie_scores))
    scores: dict[int, float] = {}
    for movie_id, feature_id in db.execute(stmt):
        scores[feature_id] = scores.get(feature_id, 0.0) + movie_scores[movie_id]
    return scores


def _accumulate_feature_candidates(
    db: Session,
    scores: dict[int, CandidateScore],
    *,
    mapping_source,
    movie_column,
    feature_column,
    feature_scores: dict[int, float],
    feature_weight: float,
    excluded_movie_ids: set[int],
    limit: int,
) -> None:
    if not feature_scores or limit <= 0:
        return

    stmt = (
        select(Movie, feature_column)
        .join(mapping_source, movie_column == Movie.id)
        .where(feature_column.in_(feature_scores))
        .where(Movie.adult.is_(False))
    )
    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))

    for movie, feature_id in db.execute(stmt):
        score = movie_score(movie) + (feature_scores[feature_id] * feature_weight)
        existing = scores.get(movie.id)
        if existing is None:
            scores[movie.id] = CandidateScore(movie_id=movie.id, score=score, source=SOURCE_CONTENT)
            continue
        scores[movie.id] = CandidateScore(movie_id=movie.id, score=existing.score + score, source=SOURCE_CONTENT)

    top_movie_ids = {
        candidate.movie_id
        for candidate in sorted(scores.values(), key=lambda candidate: candidate.score, reverse=True)[:limit]
    }
    stale_movie_ids = set(scores) - top_movie_ids
    for movie_id in stale_movie_ids:
        scores.pop(movie_id)


def _interaction_weight(
    *,
    is_pinned: bool,
    is_watched: bool,
    is_passed: bool,
    pinned_at: datetime | None = None,
    watched_at: datetime | None = None,
    passed_at: datetime | None = None,
    now: datetime | None = None,
) -> float:
    weight = 0.0
    now = now or datetime.now()
    if is_passed:
        weight += ACTION_WEIGHTS["passed"] * recency_decay(passed_at, now=now)
    if is_watched:
        weight += ACTION_WEIGHTS["watched"] * recency_decay(watched_at, now=now)
    if is_pinned:
        weight += ACTION_WEIGHTS["pinned"] * recency_decay(pinned_at, now=now)
    return weight


def recency_decay(action_at: datetime | None, *, now: datetime | None = None) -> float:
    if action_at is None:
        return 1.0

    now = now or datetime.now()
    age_days = max((now - action_at).days, 0)
    if age_days <= 30:
        return RECENCY_DECAY_30_DAYS
    if age_days <= 90:
        return RECENCY_DECAY_90_DAYS
    if age_days <= 180:
        return RECENCY_DECAY_180_DAYS
    return RECENCY_DECAY_OLDER


def _playlist_sizes(db: Session) -> dict[int, int]:
    stmt = select(PlaylistMovie.playlist_id, func.count()).group_by(PlaylistMovie.playlist_id)
    return {playlist_id: movie_count for playlist_id, movie_count in db.execute(stmt)}


def _accumulate_units(weighted_units: dict[tuple[str, int], float], *, action: str, rows) -> None:
    for movie_id, count in rows:
        weighted_units[(action, movie_id)] = weighted_units.get((action, movie_id), 0.0) + float(count)


def _accumulate_playlist_units(
    weighted_units: dict[tuple[str, int], float],
    *,
    action: str,
    playlist_sizes: dict[int, int],
    rows,
) -> None:
    for movie_id, playlist_id in rows:
        playlist_size = playlist_sizes.get(playlist_id, 0)
        if playlist_size <= 0:
            continue
        weighted_units[(action, movie_id)] = weighted_units.get((action, movie_id), 0.0) + (1.0 / playlist_size)


def _weighted_signal_score(action: str, units: float) -> float:
    base_weights = {
        "playlist_add": ACTION_WEIGHTS["playlist_add"],
        "movie_post_write": ACTION_WEIGHTS["post_write"],
        "playlist_post_write": ACTION_WEIGHTS["post_write"] * PLAYLIST_DERIVED_SIGNAL_MULTIPLIER,
        "movie_like": ACTION_WEIGHTS["like"],
        "playlist_like": ACTION_WEIGHTS["like"] * PLAYLIST_DERIVED_SIGNAL_MULTIPLIER,
        "movie_reply": ACTION_WEIGHTS["reply"],
        "playlist_reply": ACTION_WEIGHTS["reply"] * PLAYLIST_DERIVED_SIGNAL_MULTIPLIER,
    }
    return base_weights[action] * math.log2(1.0 + units)


def merge_source_scores(left: CandidateScore, right: CandidateScore) -> dict[str, float]:
    merged = dict(left.source_scores or {left.source: left.score})
    right_scores = right.source_scores or {right.source: right.score}
    for source, score in right_scores.items():
        merged[source] = merged.get(source, 0.0) + score
    return merged


def build_user_process_result(
    user_id: int,
    status: str,
    reason: str | None,
    candidates: list[CandidateScore],
) -> UserProcessResult:
    source_counts: dict[str, int] = {}
    source_score_totals: dict[str, float] = {}
    for candidate in candidates:
        source_counts[candidate.source] = source_counts.get(candidate.source, 0) + 1
        source_score_totals[candidate.source] = source_score_totals.get(candidate.source, 0.0) + candidate.score
    return UserProcessResult(
        user_id=user_id,
        status=status,
        reason=reason,
        candidate_count=len(candidates),
        source_counts=source_counts,
        source_score_totals=source_score_totals,
    )


def log_pipeline_summary(batch_context: BatchContext, results: list[UserProcessResult]) -> None:
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    logger.info(
        "recommendation pipeline finished users=%s statuses=%s vectors=%s",
        len(batch_context.user_ids),
        status_counts,
        len(batch_context.user_vectors),
    )


def build_user_score_vector(signals: list[InteractionSignal]) -> dict[int, float]:
    vector: dict[int, float] = {}
    for signal in signals:
        vector[signal.movie_id] = vector.get(signal.movie_id, 0.0) + signal.score
    return vector


def cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    common_movie_ids = set(left).intersection(right)
    if not common_movie_ids:
        return 0.0

    numerator = sum(left[movie_id] * right[movie_id] for movie_id in common_movie_ids)
    left_norm = math.sqrt(sum(score * score for score in left.values()))
    right_norm = math.sqrt(sum(score * score for score in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


if __name__ == "__main__":
    configure_logging()
    run_pipeline()
