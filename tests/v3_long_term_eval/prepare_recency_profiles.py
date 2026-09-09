from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from app.db.session import SessionLocal
from tests.v3_long_term_eval.movielens import (
    MappedRating,
    SelectedUser,
    UserSummary,
    eligible_summary,
    iter_mapped_user_ratings,
    load_movielens_genres,
    load_movielens_links,
    positive_genre_weights,
    summarize_user,
    temporal_split,
)
from tests.v3_long_term_eval.prepare import load_database_movie_ids, write_plan


ROOT = Path("tests/v3_long_term_eval")
OUTPUT_DIR = ROOT / "generated/recency_profiles"
PROFILE_TYPES = ("stable_focused", "stable_diverse", "changing")


@dataclass(frozen=True, slots=True)
class ProfileCandidate:
    summary: UserSummary
    temporal_genre_shift: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare balanced taste-stability cohorts for recency comparison"
    )
    parser.add_argument("--users-per-profile", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--min-mapped-ratings", type=int, default=100)
    parser.add_argument("--min-train-positives", type=int, default=20)
    parser.add_argument("--min-holdout-positives", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_to_tmdb = load_movielens_links(ROOT / "inputs/links.csv")
    genres = load_movielens_genres(ROOT / "inputs/movies.csv")
    with SessionLocal() as db:
        tmdb_to_db = load_database_movie_ids(db, source_to_tmdb.values())
        db.rollback()
    source_to_db = {
        source_movie_id: tmdb_to_db[tmdb_id]
        for source_movie_id, tmdb_id in source_to_tmdb.items()
        if tmdb_id in tmdb_to_db
    }

    candidates: list[ProfileCandidate] = []
    for source_user_id, ratings in iter_mapped_user_ratings(
        ROOT / "inputs/ratings.csv",
        source_to_db_movie_id=source_to_db,
    ):
        summary = summarize_user(
            source_user_id,
            ratings,
            genres_by_source_movie_id=genres,
        )
        if not eligible_summary(
            summary,
            min_mapped_ratings=args.min_mapped_ratings,
            min_train_positives=args.min_train_positives,
            min_holdout_positives=args.min_holdout_positives,
        ):
            continue
        train, _ = temporal_split(ratings)
        shift = temporal_genre_shift(
            train,
            genres_by_source_movie_id=genres,
        )
        if shift is not None and summary.dominant_genre is not None:
            candidates.append(ProfileCandidate(summary, shift))
    if not candidates:
        raise RuntimeError("no eligible users have enough temporal genre evidence")

    entropies = np.asarray(
        [candidate.summary.genre_entropy for candidate in candidates],
        dtype=np.float64,
    )
    shifts = np.asarray(
        [candidate.temporal_genre_shift for candidate in candidates],
        dtype=np.float64,
    )
    entropy_low, entropy_high = np.quantile(entropies, (0.25, 0.75))
    shift_low, shift_high = np.quantile(shifts, (0.25, 0.75))
    buckets = {
        "stable_focused": [
            candidate
            for candidate in candidates
            if candidate.summary.genre_entropy <= entropy_low
            and candidate.temporal_genre_shift <= shift_low
        ],
        "stable_diverse": [
            candidate
            for candidate in candidates
            if candidate.summary.genre_entropy >= entropy_high
            and candidate.temporal_genre_shift <= shift_low
        ],
        "changing": [
            candidate
            for candidate in candidates
            if candidate.temporal_genre_shift >= shift_high
        ],
    }
    selected_by_profile = {
        profile_type: select_genre_distributed(
            values,
            count=args.users_per_profile,
            seed=args.seed,
            profile_type=profile_type,
        )
        for profile_type, values in buckets.items()
    }
    selected_profiles = {
        candidate.summary.source_user_id: profile_type
        for profile_type, values in selected_by_profile.items()
        for candidate in values
    }
    selected_order = {
        candidate.summary.source_user_id: ordinal
        for ordinal in range(args.users_per_profile)
        for profile_type in PROFILE_TYPES
        for candidate in (selected_by_profile[profile_type][ordinal],)
    }
    candidates_by_id = {
        candidate.summary.source_user_id: candidate for candidate in candidates
    }

    selected_users: list[SelectedUser] = []
    for source_user_id, ratings in iter_mapped_user_ratings(
        ROOT / "inputs/ratings.csv",
        source_to_db_movie_id=source_to_db,
    ):
        profile_type = selected_profiles.get(source_user_id)
        if profile_type is None:
            continue
        train, holdout = temporal_split(ratings)
        summary = candidates_by_id[source_user_id].summary
        selected_users.append(
            SelectedUser(
                source_user_id=source_user_id,
                profile_type=profile_type,
                candidate_count=len(holdout),
                train_ratings=train,
                holdout_ratings=holdout,
                summary=summary,
            )
        )
    selected_users.sort(key=lambda user: selected_order[user.source_user_id])

    expected = args.users_per_profile * len(PROFILE_TYPES)
    if len(selected_users) != expected:
        raise RuntimeError(
            f"selected user reload mismatch expected={expected} actual={len(selected_users)}"
        )
    as_of = existing_evaluation_as_of()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = OUTPUT_DIR / "evaluation_plan.jsonl"
    write_plan(plan_path, selected_users, as_of=as_of)

    selected_metrics = {
        profile_type: {
            "count": len(values),
            "mean_genre_entropy": float(
                np.mean([value.summary.genre_entropy for value in values])
            ),
            "mean_temporal_genre_shift": float(
                np.mean([value.temporal_genre_shift for value in values])
            ),
            "dominant_genres": dict(
                sorted(Counter(value.summary.dominant_genre for value in values).items())
            ),
        }
        for profile_type, values in selected_by_profile.items()
    }
    summary = {
        "users_per_profile": args.users_per_profile,
        "total_users": expected,
        "eligible_users_with_temporal_evidence": len(candidates),
        "classification": {
            "stable_focused": "genre entropy <= Q25 and temporal shift <= Q25",
            "stable_diverse": "genre entropy >= Q75 and temporal shift <= Q25",
            "changing": "temporal shift >= Q75",
            "genre_entropy_q25": float(entropy_low),
            "genre_entropy_q75": float(entropy_high),
            "temporal_genre_shift_q25": float(shift_low),
            "temporal_genre_shift_q75": float(shift_high),
        },
        "selected": selected_metrics,
        "plan": str(plan_path),
    }
    (OUTPUT_DIR / "preparation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)


def temporal_genre_shift(
    train: tuple[MappedRating, ...],
    *,
    genres_by_source_movie_id: dict[int, tuple[str, ...]],
) -> float | None:
    midpoint = len(train) // 2
    early = tuple(rating for rating in train[:midpoint] if rating.rating >= 3.5)
    late = tuple(rating for rating in train[midpoint:] if rating.rating >= 3.5)
    if len(early) < 5 or len(late) < 5:
        return None
    early_weights = positive_genre_weights(
        early,
        genres_by_source_movie_id=genres_by_source_movie_id,
    )
    late_weights = positive_genre_weights(
        late,
        genres_by_source_movie_id=genres_by_source_movie_id,
    )
    return jensen_shannon_distance(early_weights, late_weights)


def jensen_shannon_distance(
    left: Counter[str],
    right: Counter[str],
) -> float | None:
    keys = sorted(set(left) | set(right))
    left_total = float(sum(left.values()))
    right_total = float(sum(right.values()))
    if not keys or left_total <= 0.0 or right_total <= 0.0:
        return None
    divergence = 0.0
    for key in keys:
        left_probability = float(left[key]) / left_total
        right_probability = float(right[key]) / right_total
        midpoint = (left_probability + right_probability) / 2.0
        if left_probability > 0.0:
            divergence += 0.5 * left_probability * math.log(left_probability / midpoint)
        if right_probability > 0.0:
            divergence += 0.5 * right_probability * math.log(right_probability / midpoint)
    return math.sqrt(max(0.0, divergence / math.log(2.0)))


def select_genre_distributed(
    candidates: list[ProfileCandidate],
    *,
    count: int,
    seed: int,
    profile_type: str,
) -> tuple[ProfileCandidate, ...]:
    by_genre: dict[str, list[ProfileCandidate]] = {}
    for candidate in candidates:
        genre = candidate.summary.dominant_genre
        if genre is not None:
            by_genre.setdefault(genre, []).append(candidate)
    for genre, values in by_genre.items():
        values.sort(
            key=lambda value: (
                stable_int(
                    f"{seed}:{profile_type}:{genre}:{value.summary.source_user_id}"
                ),
                value.summary.source_user_id,
            )
        )
    selected: list[ProfileCandidate] = []
    positions = {genre: 0 for genre in by_genre}
    while len(selected) < count:
        added = False
        for genre in sorted(by_genre):
            position = positions[genre]
            if position >= len(by_genre[genre]):
                continue
            selected.append(by_genre[genre][position])
            positions[genre] += 1
            added = True
            if len(selected) == count:
                break
        if not added:
            raise ValueError(
                f"profile {profile_type} has only {len(selected)} selectable users; "
                f"requires {count}"
            )
    return tuple(selected)


def stable_int(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")


def existing_evaluation_as_of() -> datetime:
    path = ROOT / "generated/cohorts/100/evaluation_plan.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        first = json.loads(next(handle))
    return datetime.fromisoformat(str(first["evaluation_as_of"]))


if __name__ == "__main__":
    main()
