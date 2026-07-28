"""추천 배치가 공통으로 쓰는 유저 상호작용 신호 추출 + 워커 락 로직.

Rule-based/LightFM 등 배치 알고리즘이 무엇이든 "유저가 무엇을 좋아하는지"를 판단하는
기준은 동일해야 하므로, 이 모듈에 알고리즘과 무관한 공통 로직만 모아둔다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.mapping import PlaylistMovie, UserInteraction, likes
from app.models.playlist import Playlist
from app.models.post import Post
from app.models.reply import Reply

ACTION_WEIGHTS = {
    "passed": -3.0,
    "pinned": 4.0,
    "watched": 6.0,
    "playlist_add": 6.0,
    "post_write": 7.0,
    "like": 1.0,
    "reply": 1.5,
}

PLAYLIST_DERIVED_SIGNAL_MULTIPLIER = 0.5
RECENCY_DECAY_30_DAYS = 1.0
RECENCY_DECAY_90_DAYS = 0.8
RECENCY_DECAY_180_DAYS = 0.6
RECENCY_DECAY_OLDER = 0.4
WORKER_ADVISORY_LOCK_KEY = 2605210600


@dataclass(frozen=True)
class InteractionSignal:
    movie_id: int
    score: float
    exclude_from_feed: bool


# 2026.07.28 김광원
# advisory lock을 획득해 추천 배치가 중복 실행되지 않도록 막는다.
def acquire_worker_lock(db: Session) -> bool:
    return bool(db.scalar(text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": WORKER_ADVISORY_LOCK_KEY}))


# 2026.07.28 김광원
# 배치 종료 시 advisory lock을 해제한다.
def release_worker_lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": WORKER_ADVISORY_LOCK_KEY})


# 2026.07.28 김광원
# 유저의 pinned/watched/passed 직접 상호작용과 플레이리스트/게시글/좋아요/댓글에서 파생된
# 간접 신호를 합쳐 하나의 신호 목록으로 반환한다.
def load_interaction_signals(db: Session, user_id: int) -> list[InteractionSignal]:
    primary_signals = load_primary_interaction_signals(db, user_id)
    return [*primary_signals, *load_future_engagement_signals(db, user_id)]


# 2026.07.28 김광원
# UserInteraction 테이블의 pinned/watched/passed 값을 최근성 감쇠를 적용한 점수로 변환한다.
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


# 2026.07.28 김광원
# 플레이리스트 추가/게시글 작성/좋아요/댓글 등 간접 행동을 movie_id별 가중 점수로 집계한다.
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
