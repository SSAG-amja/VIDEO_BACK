from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from app.db.session import SessionLocal
from tests.v3_long_term_eval.movielens import (
    SelectedUser,
    UserSummary,
    eligible_summary,
    iter_mapped_user_ratings,
    load_movielens_genres,
    load_movielens_links,
    summarize_user,
    temporal_split,
)
from tests.v3_long_term_eval.prepare import load_database_movie_ids, write_plan
from tests.v3_long_term_eval.prepare_matched_profiles import (
    load_excluded_source_user_ids,
    numeric_summary,
    resolve_evaluation_as_of,
)
from tests.v3_long_term_eval.prepare_recency_profiles import (
    ProfileCandidate,
    stable_int,
)


ROOT = Path("tests/v3_long_term_eval")
OUTPUT_DIR = ROOT / "generated/continuous_entropy_profiles"
PROFILE_TYPES = (
    "stable_low_entropy",
    "stable_mid_entropy",
    "stable_high_entropy",
)
COHORT_NAMES = ("development_a", "development_b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare activity- and genre-matched continuous entropy cohorts"
    )
    parser.add_argument("--users-per-bin-per-cohort", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--min-mapped-ratings", type=int, default=100)
    parser.add_argument("--min-train-positives", type=int, default=20)
    parser.add_argument("--min-holdout-positives", type=int, default=10)
    parser.add_argument("--maximum-relative-activity-gap", type=float, default=0.20)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--evaluation-as-of",
        type=datetime.fromisoformat,
        help="Shared evaluation timestamp; defaults to the exclusion plans' value",
    )
    parser.add_argument(
        "--exclude-plan",
        type=Path,
        action="append",
        default=[],
        help="Evaluation plan whose source users must not be selected",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.users_per_bin_per_cohort <= 0:
        raise ValueError("users per bin per cohort must be positive")
    if not 0.0 <= args.maximum_relative_activity_gap < 1.0:
        raise ValueError("maximum relative activity gap must be in [0, 1)")

    excluded_source_user_ids = load_excluded_source_user_ids(args.exclude_plan)
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
        if source_user_id in excluded_source_user_ids:
            continue
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
        if summary.temporal_genre_shift is None or summary.dominant_genre is None:
            continue
        candidates.append(ProfileCandidate(summary, summary.temporal_genre_shift))
    if not candidates:
        raise RuntimeError("no eligible users have temporal genre evidence")

    shift_limit = float(
        np.quantile(
            [candidate.temporal_genre_shift for candidate in candidates],
            0.25,
        )
    )
    stable_candidates = [
        candidate
        for candidate in candidates
        if candidate.temporal_genre_shift <= shift_limit
    ]
    entropy_low, entropy_high = map(
        float,
        np.quantile(
            [candidate.summary.genre_entropy for candidate in stable_candidates],
            (1.0 / 3.0, 2.0 / 3.0),
        ),
    )
    buckets = {
        "stable_low_entropy": [
            candidate
            for candidate in stable_candidates
            if candidate.summary.genre_entropy <= entropy_low
        ],
        "stable_mid_entropy": [
            candidate
            for candidate in stable_candidates
            if entropy_low < candidate.summary.genre_entropy < entropy_high
        ],
        "stable_high_entropy": [
            candidate
            for candidate in stable_candidates
            if candidate.summary.genre_entropy >= entropy_high
        ],
    }
    triplet_count = args.users_per_bin_per_cohort * len(COHORT_NAMES)
    triplets = activity_genre_matched_triplets(
        buckets["stable_low_entropy"],
        buckets["stable_mid_entropy"],
        buckets["stable_high_entropy"],
        count=triplet_count,
        seed=args.seed,
        maximum_relative_activity_gap=args.maximum_relative_activity_gap,
    )

    assignment_by_id: dict[int, tuple[str, str, int]] = {}
    summaries_by_id: dict[int, UserSummary] = {}
    for ordinal, triplet in enumerate(triplets):
        cohort_name = COHORT_NAMES[ordinal % len(COHORT_NAMES)]
        for profile_type, candidate in zip(PROFILE_TYPES, triplet, strict=True):
            source_user_id = candidate.summary.source_user_id
            assignment_by_id[source_user_id] = (cohort_name, profile_type, ordinal)
            summaries_by_id[source_user_id] = candidate.summary

    selected_by_cohort = {name: [] for name in COHORT_NAMES}
    for source_user_id, ratings in iter_mapped_user_ratings(
        ROOT / "inputs/ratings.csv",
        source_to_db_movie_id=source_to_db,
    ):
        assignment = assignment_by_id.get(source_user_id)
        if assignment is None:
            continue
        cohort_name, profile_type, _ordinal = assignment
        train, holdout = temporal_split(ratings)
        selected_by_cohort[cohort_name].append(
            SelectedUser(
                source_user_id=source_user_id,
                profile_type=profile_type,
                candidate_count=len(holdout),
                train_ratings=train,
                holdout_ratings=holdout,
                summary=summaries_by_id[source_user_id],
            )
        )

    expected_users_per_cohort = args.users_per_bin_per_cohort * len(PROFILE_TYPES)
    as_of = resolve_evaluation_as_of(
        explicit=args.evaluation_as_of,
        exclusion_plans=args.exclude_plan,
    )
    outputs = {}
    for cohort_name, users in selected_by_cohort.items():
        users.sort(
            key=lambda user: (
                PROFILE_TYPES.index(user.profile_type),
                assignment_by_id[user.source_user_id][2],
            )
        )
        if len(users) != expected_users_per_cohort:
            raise RuntimeError(
                f"{cohort_name} selected user mismatch "
                f"expected={expected_users_per_cohort} actual={len(users)}"
            )
        path = args.output_dir / cohort_name / "evaluation_plan.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(path, users, as_of=as_of)
        outputs[cohort_name] = str(path)

    relative_activity_gaps = [triplet_relative_activity_gap(row) for row in triplets]
    summary = {
        "format_version": 1,
        "prepared_at": datetime.now().astimezone().isoformat(),
        "seed": args.seed,
        "evaluation_as_of": as_of.isoformat(),
        "excluded_source_user_count": len(excluded_source_user_ids),
        "exclude_plans": [str(path) for path in args.exclude_plan],
        "users_per_bin_per_cohort": args.users_per_bin_per_cohort,
        "users_per_cohort": expected_users_per_cohort,
        "total_users": expected_users_per_cohort * len(COHORT_NAMES),
        "classification": {
            "stable": "temporal genre shift <= eligible Q25",
            "temporal_genre_shift_q25": shift_limit,
            "entropy_low_upper_inclusive": entropy_low,
            "entropy_high_lower_inclusive": entropy_high,
            "bins_cover_stable_population_without_gaps": True,
        },
        "eligible_stable_candidate_count": len(stable_candidates),
        "eligible_bins": {
            name: candidate_summary(values) for name, values in buckets.items()
        },
        "matching": {
            "key": "same dominant genre and similar mapped rating count",
            "triplet_count": len(triplets),
            "maximum_allowed_relative_activity_gap": args.maximum_relative_activity_gap,
            "mean_relative_activity_gap": statistics.mean(relative_activity_gaps),
            "max_relative_activity_gap": max(relative_activity_gaps),
        },
        "cohorts": {
            name: selected_cohort_summary(users)
            for name, users in selected_by_cohort.items()
        },
        "outputs": outputs,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "preparation_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)


def activity_genre_matched_triplets(
    low: list[ProfileCandidate],
    middle: list[ProfileCandidate],
    high: list[ProfileCandidate],
    *,
    count: int,
    seed: int,
    maximum_relative_activity_gap: float,
) -> tuple[tuple[ProfileCandidate, ProfileCandidate, ProfileCandidate], ...]:
    if count <= 0:
        raise ValueError("matched triplet count must be positive")
    grouped = tuple(group_candidates_by_genre(values) for values in (low, middle, high))
    genres = sorted(set(grouped[0]) & set(grouped[1]) & set(grouped[2]))
    if not genres:
        raise ValueError("entropy buckets have no shared dominant genres")
    for bucket_index, by_genre in enumerate(grouped):
        for genre, values in by_genre.items():
            values.sort(
                key=lambda candidate: (
                    stable_int(
                        f"{seed}:{bucket_index}:{genre}:"
                        f"{candidate.summary.source_user_id}"
                    ),
                    candidate.summary.source_user_id,
                )
            )

    result = []
    while len(result) < count:
        added = False
        for genre in genres:
            match = closest_activity_triplet(
                grouped[0].get(genre, []),
                grouped[1].get(genre, []),
                grouped[2].get(genre, []),
                maximum_relative_activity_gap=maximum_relative_activity_gap,
            )
            if match is None:
                continue
            for values, candidate in zip(
                (grouped[0][genre], grouped[1][genre], grouped[2][genre]),
                match,
                strict=True,
            ):
                values.remove(candidate)
            result.append(match)
            added = True
            if len(result) == count:
                break
        if not added:
            raise ValueError(
                f"only {len(result)} activity-matched entropy triplets are available; "
                f"requires {count}"
            )
    return tuple(result)


def closest_activity_triplet(
    low: list[ProfileCandidate],
    middle: list[ProfileCandidate],
    high: list[ProfileCandidate],
    *,
    maximum_relative_activity_gap: float,
) -> tuple[ProfileCandidate, ProfileCandidate, ProfileCandidate] | None:
    while low and middle and high:
        anchor = low[0]
        anchor_count = anchor.summary.mapped_rating_count
        middle_candidates = sorted(
            middle,
            key=lambda value: activity_distance(value, anchor_count),
        )[:24]
        high_candidates = sorted(
            high,
            key=lambda value: activity_distance(value, anchor_count),
        )[:24]
        valid = []
        for middle_candidate in middle_candidates:
            for high_candidate in high_candidates:
                triplet = (anchor, middle_candidate, high_candidate)
                gap = triplet_relative_activity_gap(triplet)
                if gap <= maximum_relative_activity_gap:
                    counts = [value.summary.mapped_rating_count for value in triplet]
                    spread = max(math.log1p(value) for value in counts) - min(
                        math.log1p(value) for value in counts
                    )
                    valid.append((spread, gap, middle_candidate, high_candidate))
        if valid:
            _spread, _gap, middle_candidate, high_candidate = min(
                valid,
                key=lambda value: (
                    value[0],
                    value[1],
                    value[2].summary.source_user_id,
                    value[3].summary.source_user_id,
                ),
            )
            return anchor, middle_candidate, high_candidate
        low.pop(0)
    return None


def activity_distance(candidate: ProfileCandidate, anchor_count: int) -> tuple[float, int]:
    return (
        abs(
            math.log1p(candidate.summary.mapped_rating_count)
            - math.log1p(anchor_count)
        ),
        candidate.summary.source_user_id,
    )


def triplet_relative_activity_gap(
    triplet: tuple[ProfileCandidate, ProfileCandidate, ProfileCandidate],
) -> float:
    counts = [candidate.summary.mapped_rating_count for candidate in triplet]
    return (max(counts) - min(counts)) / max(counts)


def group_candidates_by_genre(
    candidates: list[ProfileCandidate],
) -> dict[str, list[ProfileCandidate]]:
    result: dict[str, list[ProfileCandidate]] = {}
    for candidate in candidates:
        genre = candidate.summary.dominant_genre
        if genre is not None:
            result.setdefault(genre, []).append(candidate)
    return result


def candidate_summary(candidates: list[ProfileCandidate]) -> dict:
    return {
        "count": len(candidates),
        "genre_entropy": numeric_float_summary(
            [candidate.summary.genre_entropy for candidate in candidates]
        ),
        "mapped_rating_count": numeric_summary(
            [candidate.summary.mapped_rating_count for candidate in candidates]
        ),
        "dominant_genres": dict(
            sorted(Counter(candidate.summary.dominant_genre for candidate in candidates).items())
        ),
    }


def selected_cohort_summary(users: list[SelectedUser]) -> dict:
    return {
        "count": len(users),
        "profile_type_counts": dict(
            sorted(Counter(user.profile_type for user in users).items())
        ),
        "profiles": {
            profile_type: {
                "genre_entropy": numeric_float_summary(
                    [
                        user.summary.genre_entropy
                        for user in users
                        if user.profile_type == profile_type
                    ]
                ),
                "mapped_rating_count": numeric_summary(
                    [
                        user.summary.mapped_rating_count
                        for user in users
                        if user.profile_type == profile_type
                    ]
                ),
                "dominant_genres": dict(
                    sorted(
                        Counter(
                            user.summary.dominant_genre
                            for user in users
                            if user.profile_type == profile_type
                        ).items()
                    )
                ),
            }
            for profile_type in PROFILE_TYPES
        },
    }


def numeric_float_summary(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "max": max(values),
    }


if __name__ == "__main__":
    main()
