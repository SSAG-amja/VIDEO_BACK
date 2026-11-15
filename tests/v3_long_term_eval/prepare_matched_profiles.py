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
    eligible_summary,
    iter_mapped_user_ratings,
    load_movielens_genres,
    load_movielens_links,
    summarize_user,
    temporal_split,
)
from tests.v3_long_term_eval.prepare import (
    build_seed_sql,
    load_database_movie_ids,
    write_plan,
)
from tests.v3_long_term_eval.prepare_recency_profiles import (
    ProfileCandidate,
    stable_int,
)


ROOT = Path("tests/v3_long_term_eval")
OUTPUT_DIR = ROOT / "generated/matched_profiles"
PROFILE_TYPES = ("stable_focused", "stable_diverse")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare activity-matched stable focused/diverse cohorts"
    )
    parser.add_argument("--users-per-profile", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--min-mapped-ratings", type=int, default=100)
    parser.add_argument("--min-train-positives", type=int, default=20)
    parser.add_argument("--min-holdout-positives", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--evaluation-as-of",
        type=parse_datetime,
        help=(
            "Evaluation timestamp in ISO 8601 format. When omitted, all exclusion "
            "plans must contain the same evaluation_as_of value."
        ),
    )
    parser.add_argument(
        "--exclude-plan",
        type=Path,
        action="append",
        default=[],
        help="Evaluation plan whose source users must not be selected",
    )
    parser.add_argument(
        "--write-profile-plans",
        action="store_true",
        help="Also write one duplicate evaluation plan per profile type",
    )
    parser.add_argument(
        "--write-seed-sql",
        action="store_true",
        help="Also write SQL that inserts the selected users into the database",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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

    entropies = np.asarray(
        [candidate.summary.genre_entropy for candidate in candidates],
        dtype=np.float64,
    )
    shifts = np.asarray(
        [candidate.temporal_genre_shift for candidate in candidates],
        dtype=np.float64,
    )
    entropy_low, entropy_high = np.quantile(entropies, (0.25, 0.75))
    shift_low = float(np.quantile(shifts, 0.25))
    focused = [
        candidate
        for candidate in candidates
        if candidate.summary.genre_entropy <= entropy_low
        and candidate.temporal_genre_shift <= shift_low
    ]
    diverse = [
        candidate
        for candidate in candidates
        if candidate.summary.genre_entropy >= entropy_high
        and candidate.temporal_genre_shift <= shift_low
    ]
    pairs = activity_matched_pairs(
        focused,
        diverse,
        count=args.users_per_profile,
        seed=args.seed,
        maximum_relative_activity_gap=0.20,
    )
    selected_profile_by_id = {
        candidate.summary.source_user_id: profile
        for pair in pairs
        for profile, candidate in zip(PROFILE_TYPES, pair, strict=True)
    }
    selected_order = {
        candidate.summary.source_user_id: ordinal
        for ordinal, pair in enumerate(pairs)
        for candidate in pair
    }
    summaries_by_id = {
        candidate.summary.source_user_id: candidate.summary
        for pair in pairs
        for candidate in pair
    }

    selected_users: list[SelectedUser] = []
    for source_user_id, ratings in iter_mapped_user_ratings(
        ROOT / "inputs/ratings.csv",
        source_to_db_movie_id=source_to_db,
    ):
        profile = selected_profile_by_id.get(source_user_id)
        if profile is None:
            continue
        train, holdout = temporal_split(ratings)
        selected_users.append(
            SelectedUser(
                source_user_id=source_user_id,
                profile_type=profile,
                candidate_count=len(holdout),
                train_ratings=train,
                holdout_ratings=holdout,
                summary=summaries_by_id[source_user_id],
            )
        )
    selected_users.sort(
        key=lambda user: (PROFILE_TYPES.index(user.profile_type), selected_order[user.source_user_id])
    )
    expected_count = args.users_per_profile * len(PROFILE_TYPES)
    if len(selected_users) != expected_count:
        raise RuntimeError(
            f"selected user reload mismatch expected={expected_count} actual={len(selected_users)}"
        )

    by_profile = {
        profile: [user for user in selected_users if user.profile_type == profile]
        for profile in PROFILE_TYPES
    }
    as_of = resolve_evaluation_as_of(
        explicit=args.evaluation_as_of,
        exclusion_plans=args.exclude_plan,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    combined_plan = args.output_dir / "evaluation_plan.jsonl"
    write_plan(combined_plan, selected_users, as_of=as_of)
    outputs = {"combined_plan": str(combined_plan)}
    if args.write_profile_plans:
        profile_plans = {}
        for profile, users in by_profile.items():
            path = args.output_dir / profile / "evaluation_plan.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            write_plan(path, users, as_of=as_of)
            profile_plans[profile] = str(path)
        outputs["profile_plans"] = profile_plans
    if args.write_seed_sql:
        seed_path = args.output_dir / "01_seed_movielens_users.sql"
        seed_path.write_text(
            build_seed_sql(selected_users, as_of=as_of), encoding="utf-8"
        )
        outputs["seed_sql"] = str(seed_path)

    pair_relative_gaps = [
        abs(left.summary.mapped_rating_count - right.summary.mapped_rating_count)
        / max(left.summary.mapped_rating_count, right.summary.mapped_rating_count)
        for left, right in pairs
    ]
    summary = {
        "format_version": 1,
        "prepared_at": datetime.now().astimezone().isoformat(),
        "seed": args.seed,
        "evaluation_as_of": as_of.isoformat(),
        "excluded_source_user_count": len(excluded_source_user_ids),
        "exclude_plans": [str(path) for path in args.exclude_plan],
        "users_per_profile": args.users_per_profile,
        "total_users": expected_count,
        "classification": {
            "stable_focused": "genre entropy <= Q25 and temporal shift <= Q25",
            "stable_diverse": "genre entropy >= Q75 and temporal shift <= Q25",
            "genre_entropy_q25": float(entropy_low),
            "genre_entropy_q75": float(entropy_high),
            "temporal_genre_shift_q25": shift_low,
        },
        "eligible_candidate_pool": {
            "stable_focused": candidate_pool_summary(focused),
            "stable_diverse": candidate_pool_summary(diverse),
        },
        "matching": {
            "key": "mapped rating count within the same dominant genre",
            "maximum_allowed_relative_activity_gap": 0.20,
            "same_dominant_genre_pairs": sum(
                left.summary.dominant_genre == right.summary.dominant_genre
                for left, right in pairs
            ),
            "mean_relative_activity_gap": statistics.mean(pair_relative_gaps),
            "max_relative_activity_gap": max(pair_relative_gaps),
        },
        "profiles": {
            profile: profile_summary(users) for profile, users in by_profile.items()
        },
        "outputs": outputs,
    }
    summary_path = args.output_dir / "preparation_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)


def load_excluded_source_user_ids(paths: list[Path]) -> frozenset[int]:
    source_user_ids: set[int] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"excluded evaluation plan does not exist: {path}")
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                try:
                    source_user_ids.add(int(row["source_user_id"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid source_user_id at {path}:{line_number}"
                    ) from exc
    return frozenset(source_user_ids)


def activity_matched_pairs(
    focused: list[ProfileCandidate],
    diverse: list[ProfileCandidate],
    *,
    count: int,
    seed: int,
    maximum_relative_activity_gap: float = 0.20,
) -> tuple[tuple[ProfileCandidate, ProfileCandidate], ...]:
    if count <= 0:
        raise ValueError("matched profile count must be positive")
    if not 0.0 <= maximum_relative_activity_gap < 1.0:
        raise ValueError("maximum relative activity gap must be in [0, 1)")
    focused_by_genre = group_by_genre(focused)
    diverse_by_genre = group_by_genre(diverse)
    genres = sorted(focused_by_genre.keys() & diverse_by_genre.keys())
    if not genres:
        raise ValueError("focused and diverse profiles have no shared dominant genres")
    for genre in genres:
        focused_by_genre[genre].sort(
            key=lambda value: (
                stable_int(f"{seed}:focused:{genre}:{value.summary.source_user_id}"),
                value.summary.source_user_id,
            )
        )

    focused_positions = {genre: 0 for genre in genres}
    unused_diverse = {
        genre: list(values) for genre, values in diverse_by_genre.items() if genre in genres
    }
    pairs: list[tuple[ProfileCandidate, ProfileCandidate]] = []
    while len(pairs) < count:
        added = False
        for genre in genres:
            while (
                focused_positions[genre] < len(focused_by_genre[genre])
                and unused_diverse[genre]
            ):
                position = focused_positions[genre]
                left = focused_by_genre[genre][position]
                focused_positions[genre] += 1
                right = min(
                    unused_diverse[genre],
                    key=lambda value: (
                        abs(
                            math.log1p(value.summary.mapped_rating_count)
                            - math.log1p(left.summary.mapped_rating_count)
                        ),
                        stable_int(
                            f"{seed}:diverse:{genre}:{left.summary.source_user_id}:"
                            f"{value.summary.source_user_id}"
                        ),
                        value.summary.source_user_id,
                    ),
                )
                relative_gap = abs(
                    left.summary.mapped_rating_count
                    - right.summary.mapped_rating_count
                ) / max(
                    left.summary.mapped_rating_count,
                    right.summary.mapped_rating_count,
                )
                if relative_gap > maximum_relative_activity_gap:
                    continue
                unused_diverse[genre].remove(right)
                pairs.append((left, right))
                added = True
                break
            if len(pairs) == count:
                break
        if not added:
            raise ValueError(
                f"only {len(pairs)} activity-matched profile pairs are available; "
                f"requires {count}"
            )
    return tuple(pairs)


def group_by_genre(candidates: list[ProfileCandidate]) -> dict[str, list[ProfileCandidate]]:
    result: dict[str, list[ProfileCandidate]] = {}
    for candidate in candidates:
        genre = candidate.summary.dominant_genre
        if genre is not None:
            result.setdefault(genre, []).append(candidate)
    return result


def profile_summary(users: list[SelectedUser]) -> dict:
    mapped_counts = [user.summary.mapped_rating_count for user in users]
    holdout_counts = [user.candidate_count for user in users]
    return {
        "count": len(users),
        "mapped_rating_count": numeric_summary(mapped_counts),
        "holdout_count": numeric_summary(holdout_counts),
        "genre_entropy_mean": statistics.mean(
            user.summary.genre_entropy for user in users
        ),
        "temporal_genre_shift_mean": statistics.mean(
            float(user.summary.temporal_genre_shift) for user in users
        ),
        "dominant_genres": dict(
            sorted(Counter(user.summary.dominant_genre for user in users).items())
        ),
    }


def candidate_pool_summary(candidates: list[ProfileCandidate]) -> dict:
    return {
        "count": len(candidates),
        "dominant_genres": dict(
            sorted(
                Counter(candidate.summary.dominant_genre for candidate in candidates).items()
            )
        ),
    }


def numeric_summary(values: list[int]) -> dict[str, float | int]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "max": max(values),
    }


def parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO 8601 evaluation timestamp: {value}"
        ) from exc
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("evaluation timestamp must include a timezone")
    return parsed


def resolve_evaluation_as_of(
    *,
    explicit: datetime | None,
    exclusion_plans: list[Path],
) -> datetime:
    if explicit is not None:
        return explicit
    if not exclusion_plans:
        raise ValueError(
            "evaluation_as_of requires --evaluation-as-of or at least one "
            "--exclude-plan"
        )

    values = {evaluation_as_of_from_plan(path) for path in exclusion_plans}
    if len(values) != 1:
        rendered = ", ".join(sorted(value.isoformat() for value in values))
        raise ValueError(f"exclusion plans use different evaluation_as_of values: {rendered}")
    return next(iter(values))


def evaluation_as_of_from_plan(path: Path) -> datetime:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                value = str(json.loads(raw_line)["evaluation_as_of"])
                return parse_datetime(value)
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"invalid evaluation_as_of at {path}:{line_number}"
                ) from exc
    raise ValueError(f"evaluation plan is empty: {path}")


if __name__ == "__main__":
    main()
