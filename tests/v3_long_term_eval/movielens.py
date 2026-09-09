from __future__ import annotations

import csv
import hashlib
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Iterable, Iterator, Mapping


PROFILE_TYPES = (
    "light_focused",
    "light_diverse",
    "dense_focused",
    "dense_diverse",
)


@dataclass(frozen=True, slots=True)
class MappedRating:
    source_movie_id: int
    movie_id: int
    rating: float
    timestamp: int


@dataclass(frozen=True, slots=True)
class UserSummary:
    source_user_id: int
    mapped_rating_count: int
    train_rating_count: int
    train_positive_count: int
    holdout_positive_count: int
    genre_entropy: float
    dominant_genre: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionThresholds:
    activity_median: float
    genre_entropy_median: float


@dataclass(frozen=True, slots=True)
class SelectedUser:
    source_user_id: int
    profile_type: str
    candidate_count: int
    train_ratings: tuple[MappedRating, ...]
    holdout_ratings: tuple[MappedRating, ...]
    summary: UserSummary


def load_movielens_links(path: Path) -> dict[int, int]:
    result: dict[int, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tmdb_value = (row.get("tmdbId") or "").strip()
            if tmdb_value:
                result[int(row["movieId"])] = int(float(tmdb_value))
    return result


def load_movielens_genres(path: Path) -> dict[int, tuple[str, ...]]:
    result: dict[int, tuple[str, ...]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            genres = tuple(
                genre
                for genre in (row.get("genres") or "").split("|")
                if genre and genre != "(no genres listed)"
            )
            result[int(row["movieId"])] = genres
    return result


def iter_mapped_user_ratings(
    ratings_path: Path,
    *,
    source_to_db_movie_id: Mapping[int, int],
) -> Iterator[tuple[int, tuple[MappedRating, ...]]]:
    with ratings_path.open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        for source_user_id, grouped in groupby(rows, key=lambda row: int(row["userId"])):
            mapped_by_database_movie: dict[int, MappedRating] = {}
            for row in grouped:
                source_movie_id = int(row["movieId"])
                movie_id = source_to_db_movie_id.get(source_movie_id)
                if movie_id is None:
                    continue
                rating = MappedRating(
                    source_movie_id=source_movie_id,
                    movie_id=movie_id,
                    rating=float(row["rating"]),
                    timestamp=int(row["timestamp"]),
                )
                previous = mapped_by_database_movie.get(movie_id)
                if previous is None or (rating.timestamp, rating.source_movie_id) > (
                    previous.timestamp,
                    previous.source_movie_id,
                ):
                    mapped_by_database_movie[movie_id] = rating
            if mapped_by_database_movie:
                yield source_user_id, tuple(
                    sorted(
                        mapped_by_database_movie.values(),
                        key=lambda item: (item.timestamp, item.source_movie_id),
                    )
                )


def temporal_split(
    ratings: tuple[MappedRating, ...],
    *,
    train_ratio: float = 0.70,
) -> tuple[tuple[MappedRating, ...], tuple[MappedRating, ...]]:
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train ratio must be between zero and one")
    if len(ratings) < 2:
        return ratings, ()
    split_at = max(1, min(len(ratings) - 1, int(len(ratings) * train_ratio)))
    return ratings[:split_at], ratings[split_at:]


def summarize_user(
    source_user_id: int,
    ratings: tuple[MappedRating, ...],
    *,
    genres_by_source_movie_id: Mapping[int, tuple[str, ...]],
    train_ratio: float = 0.70,
) -> UserSummary:
    train, holdout = temporal_split(ratings, train_ratio=train_ratio)
    positive_train = tuple(item for item in train if item.rating >= 3.5)
    genre_weights = positive_genre_weights(
        positive_train,
        genres_by_source_movie_id=genres_by_source_movie_id,
    )
    return UserSummary(
        source_user_id=source_user_id,
        mapped_rating_count=len(ratings),
        train_rating_count=len(train),
        train_positive_count=len(positive_train),
        holdout_positive_count=sum(item.rating >= 3.5 for item in holdout),
        genre_entropy=normalized_genre_entropy_from_weights(genre_weights),
        dominant_genre=dominant_genre_from_weights(genre_weights),
    )


def normalized_genre_entropy(
    ratings: Iterable[MappedRating],
    *,
    genres_by_source_movie_id: Mapping[int, tuple[str, ...]],
) -> float:
    weights = positive_genre_weights(
        ratings,
        genres_by_source_movie_id=genres_by_source_movie_id,
    )
    return normalized_genre_entropy_from_weights(weights)


def positive_genre_weights(
    ratings: Iterable[MappedRating],
    *,
    genres_by_source_movie_id: Mapping[int, tuple[str, ...]],
) -> Counter[str]:
    weights: Counter[str] = Counter()
    for rating in ratings:
        genres = genres_by_source_movie_id.get(rating.source_movie_id, ())
        if not genres:
            continue
        contribution = 1.0 / len(genres)
        for genre in genres:
            weights[genre] += contribution
    return weights


def normalized_genre_entropy_from_weights(weights: Mapping[str, float]) -> float:
    if len(weights) <= 1:
        return 0.0
    total = sum(weights.values())
    entropy = -sum(
        (weight / total) * math.log(weight / total)
        for weight in weights.values()
        if weight > 0.0
    )
    return entropy / math.log(len(weights))


def dominant_genre_from_weights(weights: Mapping[str, float]) -> str | None:
    if not weights:
        return None
    return min(weights, key=lambda genre: (-weights[genre], genre))


def eligible_summary(
    summary: UserSummary,
    *,
    min_mapped_ratings: int,
    min_train_positives: int,
    min_holdout_positives: int,
) -> bool:
    return (
        summary.mapped_rating_count >= min_mapped_ratings
        and summary.train_positive_count >= min_train_positives
        and summary.holdout_positive_count >= min_holdout_positives
    )


def selection_thresholds(summaries: Iterable[UserSummary]) -> SelectionThresholds:
    values = tuple(summaries)
    if not values:
        raise ValueError("no eligible MovieLens users were found")
    return SelectionThresholds(
        activity_median=float(statistics.median(item.mapped_rating_count for item in values)),
        genre_entropy_median=float(statistics.median(item.genre_entropy for item in values)),
    )


def classify_user(summary: UserSummary, thresholds: SelectionThresholds) -> str:
    activity = "dense" if summary.mapped_rating_count >= thresholds.activity_median else "light"
    diversity = "diverse" if summary.genre_entropy >= thresholds.genre_entropy_median else "focused"
    return f"{activity}_{diversity}"


def select_balanced_user_ids(
    summaries: Iterable[UserSummary],
    *,
    thresholds: SelectionThresholds,
    users_per_type: int,
    seed: int,
) -> dict[int, str]:
    sequence = select_balanced_user_sequence(
        summaries,
        thresholds=thresholds,
        user_count=users_per_type * len(PROFILE_TYPES),
        seed=seed,
    )
    return dict(sequence)


def select_balanced_user_sequence(
    summaries: Iterable[UserSummary],
    *,
    thresholds: SelectionThresholds,
    user_count: int,
    seed: int,
) -> tuple[tuple[int, str], ...]:
    if user_count <= 0:
        raise ValueError("user count must be positive")
    buckets: dict[str, list[UserSummary]] = {name: [] for name in PROFILE_TYPES}
    for summary in summaries:
        buckets[classify_user(summary, thresholds)].append(summary)
    ordered_buckets: dict[str, list[UserSummary]] = {}
    for profile_type in PROFILE_TYPES:
        ordered_buckets[profile_type] = sorted(
            buckets[profile_type],
            key=lambda item: (_stable_int(f"{seed}:user:{item.source_user_id}"), item.source_user_id),
        )
        required = (user_count + len(PROFILE_TYPES) - 1) // len(PROFILE_TYPES)
        if len(ordered_buckets[profile_type]) < required:
            raise ValueError(
                f"profile type {profile_type} has only "
                f"{len(ordered_buckets[profile_type])} eligible users; "
                f"requires up to {required}"
            )

    selected: list[tuple[int, str]] = []
    for ordinal in range(user_count):
        profile_type = PROFILE_TYPES[ordinal % len(PROFILE_TYPES)]
        bucket_index = ordinal // len(PROFILE_TYPES)
        summary = ordered_buckets[profile_type][bucket_index]
        selected.append((summary.source_user_id, profile_type))
    return tuple(selected)


def select_focused_user_sequence(
    summaries: Iterable[UserSummary],
    *,
    thresholds: SelectionThresholds,
    user_count: int,
    seed: int,
) -> tuple[tuple[int, str], ...]:
    if user_count <= 0:
        raise ValueError("user count must be positive")
    buckets: dict[str, list[UserSummary]] = {}
    for summary in summaries:
        if (
            summary.genre_entropy >= thresholds.genre_entropy_median
            or summary.dominant_genre is None
        ):
            continue
        buckets.setdefault(summary.dominant_genre, []).append(summary)
    for genre, values in buckets.items():
        buckets[genre] = sorted(
            values,
            key=lambda item: (
                _stable_int(f"{seed}:focused:{genre}:{item.source_user_id}"),
                item.source_user_id,
            ),
        )

    selected: list[tuple[int, str]] = []
    positions = {genre: 0 for genre in buckets}
    genres = sorted(buckets)
    while len(selected) < user_count:
        added = False
        for genre in genres:
            position = positions[genre]
            if position >= len(buckets[genre]):
                continue
            summary = buckets[genre][position]
            positions[genre] += 1
            selected.append((summary.source_user_id, "focused"))
            added = True
            if len(selected) == user_count:
                break
        if not added:
            raise ValueError(
                f"only {len(selected)} focused users with a dominant genre are available; "
                f"requires {user_count}"
            )
    return tuple(selected)


def _stable_int(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")
