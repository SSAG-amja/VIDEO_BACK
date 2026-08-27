from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.datasets.dataset_schemas import (
    SocialProjectionDiagnostics,
    SocialProjectionResult,
    SocialRawSignal,
    SocialSignalAction,
)
from app.models.mapping import PlaylistMovie, likes
from app.models.movie import Movie
from app.models.post import Post
from app.models.reply import Reply
from app.models.user import User
from app.services.recsys.v3.domain.catalog import eligible_catalog_movie_clause


DIRECTION_UNRESOLVED = "direction_unresolved"


def project_social_raw_signals(
    db: Session,
    *,
    data_cutoff_at: datetime,
    include_undated_movie_likes: bool,
) -> SocialProjectionResult:
    cutoff_at = normalize_datetime(data_cutoff_at)
    signals: list[SocialRawSignal] = []
    event_counts: Counter[str] = Counter()
    deferred_event_counts: Counter[str] = Counter()

    movie_post_rows = db.execute(
        select(
            Post.id,
            Post.user_id,
            Post.movie_id,
            Post.created_at,
        )
        .join(User, User.id == Post.user_id)
        .join(Movie, Movie.id == Post.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            Post.is_playlist.is_(False),
            Post.movie_id.is_not(None),
            Post.created_at <= cutoff_at,
        )
    )
    for post_id, user_id, movie_id, occurred_at in movie_post_rows:
        signals.append(
            build_raw_signal(
                user_id=user_id,
                movie_id=movie_id,
                action=SocialSignalAction.MOVIE_POST_WRITE,
                source_id=post_id,
                post_id=post_id,
                occurred_at=occurred_at,
            )
        )
        event_counts[SocialSignalAction.MOVIE_POST_WRITE.value] += 1

    playlist_post_rows = db.execute(
        select(
            Post.id,
            Post.user_id,
            Post.id.label("post_id"),
            Post.playlist_id,
            PlaylistMovie.movie_id,
            Post.created_at,
        )
        .join(User, User.id == Post.user_id)
        .join(PlaylistMovie, PlaylistMovie.playlist_id == Post.playlist_id)
        .join(Movie, Movie.id == PlaylistMovie.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            Post.is_playlist.is_(True),
            Post.playlist_id.is_not(None),
            Post.created_at <= cutoff_at,
            PlaylistMovie.created_at <= Post.created_at,
        )
    )
    projected, projected_events = project_playlist_rows(
        playlist_post_rows,
        action=SocialSignalAction.PLAYLIST_POST_WRITE,
    )
    signals.extend(projected)
    event_counts[SocialSignalAction.PLAYLIST_POST_WRITE.value] += projected_events

    if include_undated_movie_likes:
        movie_like_rows = db.execute(
            select(
                likes.c.post_id,
                likes.c.user_id,
                Post.movie_id,
            )
            .select_from(likes)
            .join(User, User.id == likes.c.user_id)
            .join(Post, Post.id == likes.c.post_id)
            .join(Movie, Movie.id == Post.movie_id)
            .where(
                User.deleted_at.is_(None),
                *eligible_catalog_movie_clause(),
                Post.is_playlist.is_(False),
                Post.movie_id.is_not(None),
                Post.created_at <= cutoff_at,
                or_(Post.user_id.is_(None), Post.user_id != likes.c.user_id),
            )
        )
        for post_id, user_id, movie_id in movie_like_rows:
            signals.append(
                build_raw_signal(
                    user_id=user_id,
                    movie_id=movie_id,
                    action=SocialSignalAction.MOVIE_POST_LIKE,
                    source_id=post_id,
                    post_id=post_id,
                    occurred_at=None,
                )
            )
            event_counts[SocialSignalAction.MOVIE_POST_LIKE.value] += 1
    else:
        deferred_event_counts[SocialSignalAction.MOVIE_POST_LIKE.value] = count_movie_post_likes(
            db,
            data_cutoff_at=cutoff_at,
        )

    movie_reply_rows = db.execute(
        select(
            Reply.id,
            Reply.user_id,
            Post.id,
            Post.movie_id,
            Reply.created_at,
        )
        .join(User, User.id == Reply.user_id)
        .join(Post, Post.id == Reply.post_id)
        .join(Movie, Movie.id == Post.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            Post.is_playlist.is_(False),
            Post.movie_id.is_not(None),
            Reply.created_at <= cutoff_at,
            or_(Post.user_id.is_(None), Post.user_id != Reply.user_id),
        )
    )
    for reply_id, user_id, post_id, movie_id, occurred_at in movie_reply_rows:
        signals.append(
            build_raw_signal(
                user_id=user_id,
                movie_id=movie_id,
                action=SocialSignalAction.MOVIE_POST_REPLY,
                source_id=reply_id,
                post_id=post_id,
                occurred_at=occurred_at,
            )
        )
        event_counts[SocialSignalAction.MOVIE_POST_REPLY.value] += 1

    playlist_reply_rows = db.execute(
        select(
            Reply.id,
            Reply.user_id,
            Post.id.label("post_id"),
            Post.playlist_id,
            PlaylistMovie.movie_id,
            Reply.created_at,
        )
        .join(User, User.id == Reply.user_id)
        .join(Post, Post.id == Reply.post_id)
        .join(PlaylistMovie, PlaylistMovie.playlist_id == Post.playlist_id)
        .join(Movie, Movie.id == PlaylistMovie.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            Post.is_playlist.is_(True),
            Post.playlist_id.is_not(None),
            Reply.created_at <= cutoff_at,
            PlaylistMovie.created_at <= Reply.created_at,
            or_(Post.user_id.is_(None), Post.user_id != Reply.user_id),
        )
    )
    projected, projected_events = project_playlist_rows(
        playlist_reply_rows,
        action=SocialSignalAction.PLAYLIST_POST_REPLY,
    )
    signals.extend(projected)
    event_counts[SocialSignalAction.PLAYLIST_POST_REPLY.value] += projected_events

    deferred_event_counts[SocialSignalAction.PLAYLIST_POST_LIKE.value] = count_playlist_post_likes(
        db,
        data_cutoff_at=cutoff_at,
    )

    sorted_signals = tuple(sorted(signals, key=social_signal_sort_key))
    return SocialProjectionResult(
        signals=sorted_signals,
        diagnostics=build_diagnostics(
            sorted_signals,
            event_counts=event_counts,
            deferred_event_counts=deferred_event_counts,
            data_cutoff_at=cutoff_at,
        ),
    )


def build_raw_signal(
    *,
    user_id: int,
    movie_id: int,
    action: SocialSignalAction,
    source_id: int,
    post_id: int,
    occurred_at: datetime | None,
    playlist_id: int | None = None,
    distributed_unit: float = 1.0,
) -> SocialRawSignal:
    return SocialRawSignal(
        user_id=user_id,
        movie_id=movie_id,
        action=action,
        source_id=source_id,
        post_id=post_id,
        playlist_id=playlist_id,
        occurred_at=normalize_optional_datetime(occurred_at),
        distributed_unit=distributed_unit,
        eligible_for_training=False,
        eligibility_reason=DIRECTION_UNRESOLVED,
    )


def project_playlist_rows(
    rows: Iterable,
    *,
    action: SocialSignalAction,
) -> tuple[list[SocialRawSignal], int]:
    grouped_rows: dict[tuple[int, int, int, int, datetime], list[int]] = defaultdict(list)
    for source_id, user_id, post_id, playlist_id, movie_id, occurred_at in rows:
        normalized_at = normalize_datetime(occurred_at)
        grouped_rows[(source_id, user_id, post_id, playlist_id, normalized_at)].append(movie_id)

    signals: list[SocialRawSignal] = []
    for (source_id, user_id, post_id, playlist_id, occurred_at), movie_ids in sorted(grouped_rows.items()):
        unique_movie_ids = sorted(set(movie_ids))
        distributed_unit = 1.0 / len(unique_movie_ids)
        for movie_id in unique_movie_ids:
            signals.append(
                build_raw_signal(
                    user_id=user_id,
                    movie_id=movie_id,
                    action=action,
                    source_id=source_id,
                    post_id=post_id,
                    playlist_id=playlist_id,
                    occurred_at=occurred_at,
                    distributed_unit=distributed_unit,
                )
            )
    return signals, len(grouped_rows)


def count_movie_post_likes(db: Session, *, data_cutoff_at: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(likes)
        .join(User, User.id == likes.c.user_id)
        .join(Post, Post.id == likes.c.post_id)
        .join(Movie, Movie.id == Post.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            Post.is_playlist.is_(False),
            Post.movie_id.is_not(None),
            Post.created_at <= data_cutoff_at,
            or_(Post.user_id.is_(None), Post.user_id != likes.c.user_id),
        )
    )
    return int(db.scalar(stmt) or 0)


def count_playlist_post_likes(db: Session, *, data_cutoff_at: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(likes)
        .join(User, User.id == likes.c.user_id)
        .join(Post, Post.id == likes.c.post_id)
        .where(
            User.deleted_at.is_(None),
            Post.is_playlist.is_(True),
            Post.playlist_id.is_not(None),
            Post.created_at <= data_cutoff_at,
            or_(Post.user_id.is_(None), Post.user_id != likes.c.user_id),
        )
    )
    return int(db.scalar(stmt) or 0)


def build_diagnostics(
    signals: tuple[SocialRawSignal, ...],
    *,
    event_counts: Counter[str],
    deferred_event_counts: Counter[str],
    data_cutoff_at: datetime,
) -> SocialProjectionDiagnostics:
    signal_counts = Counter(signal.action.value for signal in signals)
    unit_totals: dict[str, float] = defaultdict(float)
    for signal in signals:
        unit_totals[signal.action.value] += signal.distributed_unit

    projection_hash = calculate_projection_hash(
        signals,
        deferred_event_counts=deferred_event_counts,
        data_cutoff_at=data_cutoff_at,
    )
    return SocialProjectionDiagnostics(
        raw_signal_count=len(signals),
        eligible_signal_count=sum(signal.eligible_for_training for signal in signals),
        action_signal_counts=dict(sorted(signal_counts.items())),
        action_event_counts={
            action: count
            for action, count in sorted(event_counts.items())
            if count
        },
        action_unit_totals={
            action: round(total, 6)
            for action, total in sorted(unit_totals.items())
        },
        deferred_event_counts={
            action: count
            for action, count in sorted(deferred_event_counts.items())
            if count
        },
        missing_timestamp_count=sum(signal.occurred_at is None for signal in signals),
        projection_hash=projection_hash,
    )


def calculate_projection_hash(
    signals: tuple[SocialRawSignal, ...],
    *,
    deferred_event_counts: Counter[str],
    data_cutoff_at: datetime,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"cutoff:{data_cutoff_at.isoformat()}\n".encode())
    for signal in signals:
        digest.update(
            (
                f"signal:{signal.user_id}:{signal.movie_id}:{signal.action.value}:"
                f"{signal.source_id}:{signal.post_id}:{signal.playlist_id}:"
                f"{signal.occurred_at}:{signal.distributed_unit:.12f}:"
                f"{signal.eligible_for_training}:{signal.eligibility_reason}\n"
            ).encode()
        )
    for action, count in sorted(deferred_event_counts.items()):
        digest.update(f"deferred:{action}:{count}\n".encode())
    return digest.hexdigest()


def normalize_optional_datetime(value: datetime | None) -> datetime | None:
    return normalize_datetime(value) if value is not None else None


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def social_signal_sort_key(signal: SocialRawSignal) -> tuple[int, int, str, int, int]:
    return (
        signal.user_id,
        signal.movie_id,
        signal.action.value,
        signal.source_id,
        signal.post_id,
    )
