from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
from scipy.sparse import coo_matrix
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.datasets.dataset_schemas import (
    DatasetDiagnostics,
    LightFMDatasetSnapshot,
    PositiveInteraction,
)
from app.jobs.recsys.v3.datasets.social_signal_projector import project_social_raw_signals
from app.models.mapping import PlaylistMovie, UserInteraction, user_favorite_movies
from app.models.movie import Movie
from app.models.playlist import Playlist
from app.models.user import User
from app.services.recsys.v3.domain.behavior import (
    SnapshotAction,
    SnapshotSignal,
    append_current_signal,
    normalize_datetime,
    normalize_optional_datetime,
    optional_datetime_sort_value,
    recency_multiplier,
    signal_sort_key,
)
from app.services.recsys.v3.domain.catalog import eligible_catalog_movie_clause
from app.services.recsys.v3.config import (
    TRAINING_ACTION_PRIORITY,
    TRAINING_ACTION_WEIGHTS,
    TRAINING_MAX_SAMPLE_WEIGHT,
    TRAINING_OVERLAP_CONFIDENCE_BONUS,
)


POSITIVE_ACTIONS = frozenset(
    {
        SnapshotAction.FAVORITE,
        SnapshotAction.WATCHED,
        SnapshotAction.SAVED,
        SnapshotAction.PINNED,
    }
)
ACTION_PRIORITY = {
    SnapshotAction(action): index
    for index, action in enumerate(TRAINING_ACTION_PRIORITY)
}


def build_lightfm_dataset(
    db: Session,
    *,
    data_cutoff_at: datetime | None = None,
) -> LightFMDatasetSnapshot:
    is_current_snapshot = data_cutoff_at is None
    cutoff_at = normalize_datetime(data_cutoff_at or datetime.now(timezone.utc))
    movie_ids = load_training_catalog_movie_ids(db)
    signals = load_snapshot_signals(db, data_cutoff_at=cutoff_at)
    social_projection = project_social_raw_signals(
        db,
        data_cutoff_at=cutoff_at,
        include_undated_movie_likes=is_current_snapshot,
    )
    positives, passed_by_user, watched_by_user, conflict_count = aggregate_snapshot_signals(
        signals,
        data_cutoff_at=cutoff_at,
    )

    user_ids = tuple(sorted({positive.user_id for positive in positives}))
    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    movie_id_map = {movie_id: index for index, movie_id in enumerate(movie_ids)}
    interactions, sample_weights = build_sparse_interaction_matrices(
        positives,
        user_id_map=user_id_map,
        movie_id_map=movie_id_map,
    )

    passed_frozen = freeze_movie_sets(passed_by_user)
    watched_frozen = freeze_movie_sets(watched_by_user)
    excluded_frozen = freeze_movie_sets(merge_movie_sets(passed_by_user, watched_by_user))
    action_counts = Counter(signal.action.value for signal in signals)
    dataset_hash = calculate_dataset_hash(
        data_cutoff_at=cutoff_at,
        movie_ids=movie_ids,
        positives=positives,
        passed_by_user=passed_frozen,
        watched_by_user=watched_frozen,
    )
    diagnostics = DatasetDiagnostics(
        data_cutoff_at=cutoff_at,
        catalog_movie_count=len(movie_ids),
        model_user_count=len(user_ids),
        positive_pair_count=len(positives),
        raw_signal_count=len(signals),
        action_signal_counts=dict(sorted(action_counts.items())),
        passed_movie_count=sum(len(movie_ids) for movie_ids in passed_frozen.values()),
        watched_movie_count=sum(len(movie_ids) for movie_ids in watched_frozen.values()),
        excluded_pair_count=sum(len(movie_ids) for movie_ids in excluded_frozen.values()),
        passed_positive_conflict_count=conflict_count,
        missing_timestamp_count=sum(signal.occurred_at is None for signal in signals),
        dataset_hash=dataset_hash,
        social_raw_signal_count=social_projection.diagnostics.raw_signal_count,
        social_eligible_signal_count=social_projection.diagnostics.eligible_signal_count,
        social_action_signal_counts=social_projection.diagnostics.action_signal_counts,
        social_deferred_event_counts=social_projection.diagnostics.deferred_event_counts,
        social_projection_hash=social_projection.diagnostics.projection_hash,
    )

    return LightFMDatasetSnapshot(
        data_cutoff_at=cutoff_at,
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_id_map=user_id_map,
        movie_id_map=movie_id_map,
        interactions=interactions,
        sample_weights=sample_weights,
        positives=positives,
        social_signals=social_projection.signals,
        social_projection_diagnostics=social_projection.diagnostics,
        excluded_movie_ids_by_user=excluded_frozen,
        passed_movie_ids_by_user=passed_frozen,
        watched_movie_ids_by_user=watched_frozen,
        diagnostics=diagnostics,
    )


def build_sparse_interaction_matrices(
    positives: tuple[PositiveInteraction, ...],
    *,
    user_id_map: dict[int, int],
    movie_id_map: dict[int, int],
) -> tuple[coo_matrix, coo_matrix]:
    rows = np.fromiter(
        (user_id_map[positive.user_id] for positive in positives),
        dtype=np.int32,
        count=len(positives),
    )
    columns = np.fromiter(
        (movie_id_map[positive.movie_id] for positive in positives),
        dtype=np.int32,
        count=len(positives),
    )
    interaction_values = np.ones(len(positives), dtype=np.float32)
    sample_weight_values = np.fromiter(
        (positive.sample_weight for positive in positives),
        dtype=np.float32,
        count=len(positives),
    )
    shape = (len(user_id_map), len(movie_id_map))
    interactions = coo_matrix((interaction_values, (rows, columns)), shape=shape, dtype=np.float32)
    sample_weights = coo_matrix((sample_weight_values, (rows, columns)), shape=shape, dtype=np.float32)
    if not np.array_equal(interactions.row, sample_weights.row) or not np.array_equal(
        interactions.col, sample_weights.col
    ):
        raise ValueError("interaction and sample-weight coordinates must match")
    return interactions, sample_weights


def load_training_catalog_movie_ids(db: Session) -> tuple[int, ...]:
    stmt = select(Movie.id).where(*eligible_catalog_movie_clause()).order_by(Movie.id)
    return tuple(db.scalars(stmt).all())


def load_snapshot_signals(db: Session, *, data_cutoff_at: datetime) -> list[SnapshotSignal]:
    cutoff_at = normalize_datetime(data_cutoff_at)
    signals: list[SnapshotSignal] = []

    favorite_stmt = (
        select(user_favorite_movies.c.user_id, user_favorite_movies.c.movie_id)
        .join(User, User.id == user_favorite_movies.c.user_id)
        .join(Movie, Movie.id == user_favorite_movies.c.movie_id)
        .where(User.deleted_at.is_(None), *eligible_catalog_movie_clause())
    )
    signals.extend(
        SnapshotSignal(
            user_id=user_id,
            movie_id=movie_id,
            action=SnapshotAction.FAVORITE,
            occurred_at=None,
        )
        for user_id, movie_id in db.execute(favorite_stmt)
    )

    saved_stmt = (
        select(
            Playlist.user_id,
            PlaylistMovie.movie_id,
            func.max(PlaylistMovie.created_at).label("saved_at"),
        )
        .join(Playlist, Playlist.id == PlaylistMovie.playlist_id)
        .join(User, User.id == Playlist.user_id)
        .join(Movie, Movie.id == PlaylistMovie.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            PlaylistMovie.created_at <= cutoff_at,
        )
        .group_by(Playlist.user_id, PlaylistMovie.movie_id)
    )
    signals.extend(
        SnapshotSignal(
            user_id=user_id,
            movie_id=movie_id,
            action=SnapshotAction.SAVED,
            occurred_at=normalize_optional_datetime(saved_at),
        )
        for user_id, movie_id, saved_at in db.execute(saved_stmt)
    )

    interaction_stmt = (
        select(
            UserInteraction.user_id,
            UserInteraction.movie_id,
            UserInteraction.is_pinned,
            UserInteraction.is_watched,
            UserInteraction.is_passed,
            UserInteraction.pinned_at,
            UserInteraction.watched_at,
            UserInteraction.passed_at,
        )
        .join(User, User.id == UserInteraction.user_id)
        .join(Movie, Movie.id == UserInteraction.movie_id)
        .where(
            User.deleted_at.is_(None),
            *eligible_catalog_movie_clause(),
            or_(
                UserInteraction.is_pinned.is_(True),
                UserInteraction.is_watched.is_(True),
                UserInteraction.is_passed.is_(True),
            ),
        )
    )
    for row in db.execute(interaction_stmt):
        append_current_signal(
            signals,
            user_id=row.user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.PINNED,
            enabled=row.is_pinned,
            occurred_at=row.pinned_at,
            data_cutoff_at=cutoff_at,
        )
        append_current_signal(
            signals,
            user_id=row.user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.WATCHED,
            enabled=row.is_watched,
            occurred_at=row.watched_at,
            data_cutoff_at=cutoff_at,
        )
        append_current_signal(
            signals,
            user_id=row.user_id,
            movie_id=row.movie_id,
            action=SnapshotAction.PASSED,
            enabled=row.is_passed,
            occurred_at=row.passed_at,
            data_cutoff_at=cutoff_at,
        )

    return sorted(signals, key=signal_sort_key)


def aggregate_snapshot_signals(
    signals: list[SnapshotSignal],
    *,
    data_cutoff_at: datetime,
) -> tuple[
    tuple[PositiveInteraction, ...],
    dict[int, set[int]],
    dict[int, set[int]],
    int,
]:
    cutoff_at = normalize_datetime(data_cutoff_at)
    by_pair: dict[tuple[int, int], dict[SnapshotAction, SnapshotSignal]] = defaultdict(dict)
    for signal in signals:
        key = (signal.user_id, signal.movie_id)
        existing = by_pair[key].get(signal.action)
        if existing is None or optional_datetime_sort_value(signal.occurred_at) > optional_datetime_sort_value(
            existing.occurred_at
        ):
            by_pair[key][signal.action] = signal

    positives: list[PositiveInteraction] = []
    passed_by_user: dict[int, set[int]] = defaultdict(set)
    watched_by_user: dict[int, set[int]] = defaultdict(set)
    passed_positive_conflict_count = 0

    for (user_id, movie_id), pair_signals in sorted(by_pair.items()):
        if SnapshotAction.WATCHED in pair_signals:
            watched_by_user[user_id].add(movie_id)
        if SnapshotAction.PASSED in pair_signals:
            passed_by_user[user_id].add(movie_id)

        positive_signals = [
            signal
            for action, signal in pair_signals.items()
            if action in POSITIVE_ACTIONS
        ]
        if SnapshotAction.PASSED in pair_signals:
            if positive_signals:
                passed_positive_conflict_count += 1
            continue
        if not positive_signals:
            continue

        positives.append(build_positive_interaction(positive_signals, data_cutoff_at=cutoff_at))

    return (
        tuple(sorted(positives, key=lambda item: (item.user_id, item.movie_id))),
        dict(passed_by_user),
        dict(watched_by_user),
        passed_positive_conflict_count,
    )


def build_positive_interaction(
    signals: list[SnapshotSignal],
    *,
    data_cutoff_at: datetime,
) -> PositiveInteraction:
    if not signals:
        raise ValueError("positive interaction requires at least one signal")
    if any(signal.action not in POSITIVE_ACTIONS for signal in signals):
        raise ValueError("positive interaction received a non-positive action")
    pair_ids = {(signal.user_id, signal.movie_id) for signal in signals}
    if len(pair_ids) != 1:
        raise ValueError("positive interaction signals must belong to one user-movie pair")

    weighted_signals = [
        (
            signal,
            TRAINING_ACTION_WEIGHTS[signal.action.value]
            * recency_multiplier(signal.occurred_at, data_cutoff_at=data_cutoff_at),
        )
        for signal in signals
    ]
    representative, representative_weight = max(
        weighted_signals,
        key=lambda item: (
            item[1],
            -ACTION_PRIORITY[item[0].action],
        ),
    )
    overlap_bonus = sum(
        TRAINING_OVERLAP_CONFIDENCE_BONUS
        * recency_multiplier(signal.occurred_at, data_cutoff_at=data_cutoff_at)
        for signal, _weight in weighted_signals
        if signal.action != representative.action
    )
    sample_weight = min(representative_weight + overlap_bonus, TRAINING_MAX_SAMPLE_WEIGHT)
    if not math.isfinite(sample_weight) or sample_weight <= 0:
        raise ValueError("sample weight must be finite and positive")

    user_id, movie_id = pair_ids.pop()
    occurred_times = [signal.occurred_at for signal in signals if signal.occurred_at is not None]
    actions = tuple(sorted((signal.action for signal in signals), key=ACTION_PRIORITY.__getitem__))
    return PositiveInteraction(
        user_id=user_id,
        movie_id=movie_id,
        actions=actions,
        representative_action=representative.action,
        sample_weight=round(sample_weight, 6),
        latest_at=max(occurred_times) if occurred_times else None,
    )


def calculate_dataset_hash(
    *,
    data_cutoff_at: datetime,
    movie_ids: tuple[int, ...],
    positives: tuple[PositiveInteraction, ...],
    passed_by_user: dict[int, frozenset[int]],
    watched_by_user: dict[int, frozenset[int]],
) -> str:
    digest = hashlib.sha256()
    digest.update(f"cutoff:{data_cutoff_at.isoformat()}\n".encode())
    for movie_id in movie_ids:
        digest.update(f"movie:{movie_id}\n".encode())
    for positive in positives:
        actions = ",".join(action.value for action in positive.actions)
        digest.update(
            (
                f"positive:{positive.user_id}:{positive.movie_id}:{actions}:"
                f"{positive.representative_action.value}:{positive.sample_weight:.6f}\n"
            ).encode()
        )
    for user_id, excluded_movie_ids in sorted(passed_by_user.items()):
        for movie_id in sorted(excluded_movie_ids):
            digest.update(f"passed:{user_id}:{movie_id}\n".encode())
    for user_id, excluded_movie_ids in sorted(watched_by_user.items()):
        for movie_id in sorted(excluded_movie_ids):
            digest.update(f"watched:{user_id}:{movie_id}\n".encode())
    return digest.hexdigest()


def merge_movie_sets(*sources: dict[int, set[int]]) -> dict[int, set[int]]:
    merged: dict[int, set[int]] = defaultdict(set)
    for source in sources:
        for user_id, movie_ids in source.items():
            merged[user_id].update(movie_ids)
    return dict(merged)


def freeze_movie_sets(source: dict[int, set[int]]) -> dict[int, frozenset[int]]:
    return {
        user_id: frozenset(movie_ids)
        for user_id, movie_ids in sorted(source.items())
        if movie_ids
    }
