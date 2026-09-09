from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.movie import Movie
from app.services.recsys.v3.domain.catalog import eligible_catalog_movie_clause
from tests.v3_long_term_eval.movielens import (
    MappedRating,
    SelectedUser,
    eligible_summary,
    iter_mapped_user_ratings,
    load_movielens_genres,
    load_movielens_links,
    select_focused_user_sequence,
    selection_thresholds,
    summarize_user,
    temporal_split,
)


ROOT = Path("tests/v3_long_term_eval")
PLAN_FORMAT_VERSION = 1
DEFAULT_USER_COUNTS = (100, 150, 200, 300, 500)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a temporal MovieLens holdout for V3 long-term evaluation"
    )
    parser.add_argument("--ratings", type=Path, default=ROOT / "inputs/ratings.csv")
    parser.add_argument("--links", type=Path, default=ROOT / "inputs/links.csv")
    parser.add_argument("--movies", type=Path, default=ROOT / "inputs/movies.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated")
    parser.add_argument(
        "--user-counts",
        type=parse_user_counts,
        default=DEFAULT_USER_COUNTS,
        help="Comma-separated nested cohort sizes",
    )
    parser.add_argument("--min-mapped-ratings", type=int, default=100)
    parser.add_argument("--min-train-positives", type=int, default=20)
    parser.add_argument("--min-holdout-positives", type=int, default=10)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        default=datetime.now(timezone.utc).replace(microsecond=0),
        help="Evaluation snapshot time in ISO 8601 format",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for path in (args.ratings, args.links, args.movies):
        if not path.is_file():
            raise SystemExit(f"MovieLens input does not exist: {path}")
    source_to_tmdb = load_movielens_links(args.links)
    genres = load_movielens_genres(args.movies)
    with SessionLocal() as db:
        tmdb_to_db = load_database_movie_ids(db, source_to_tmdb.values())
        db.rollback()
    source_to_db = {
        source_movie_id: tmdb_to_db[tmdb_id]
        for source_movie_id, tmdb_id in source_to_tmdb.items()
        if tmdb_id in tmdb_to_db
    }
    if not source_to_db:
        raise SystemExit("No MovieLens links matched the eligible database catalog")

    summaries = []
    for source_user_id, ratings in iter_mapped_user_ratings(
        args.ratings,
        source_to_db_movie_id=source_to_db,
    ):
        summary = summarize_user(
            source_user_id,
            ratings,
            genres_by_source_movie_id=genres,
            train_ratio=args.train_ratio,
        )
        if eligible_summary(
            summary,
            min_mapped_ratings=args.min_mapped_ratings,
            min_train_positives=args.min_train_positives,
            min_holdout_positives=args.min_holdout_positives,
        ):
            summaries.append(summary)

    thresholds = selection_thresholds(summaries)
    selected_sequence = select_focused_user_sequence(
        summaries,
        thresholds=thresholds,
        user_count=max(args.user_counts),
        seed=args.seed,
    )
    selected_types = dict(selected_sequence)
    selected_ordinals = {
        source_user_id: ordinal
        for ordinal, (source_user_id, _) in enumerate(selected_sequence)
    }
    summaries_by_id = {item.source_user_id: item for item in summaries}
    selected_users = []
    for source_user_id, ratings in iter_mapped_user_ratings(
        args.ratings,
        source_to_db_movie_id=source_to_db,
    ):
        profile_type = selected_types.get(source_user_id)
        if profile_type is None:
            continue
        train, holdout = temporal_split(ratings, train_ratio=args.train_ratio)
        selected_users.append(
            SelectedUser(
                source_user_id=source_user_id,
                profile_type=profile_type,
                candidate_count=len(holdout),
                train_ratings=train,
                holdout_ratings=holdout,
                summary=summaries_by_id[source_user_id],
            )
        )
    selected_users.sort(key=lambda item: selected_ordinals[item.source_user_id])
    expected_count = max(args.user_counts)
    if len(selected_users) != expected_count:
        raise RuntimeError(
            f"selected user reload mismatch expected={expected_count} actual={len(selected_users)}"
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    as_of = normalize_as_of(args.as_of)
    plan_path = output_dir / "evaluation_plan.jsonl"
    seed_path = output_dir / "01_seed_movielens_users.sql"
    summary_path = output_dir / "preparation_summary.json"
    write_plan(plan_path, selected_users, as_of=as_of)
    cohort_plan_paths = {}
    for user_count in args.user_counts:
        cohort_path = output_dir / "cohorts" / str(user_count) / "evaluation_plan.jsonl"
        cohort_path.parent.mkdir(parents=True, exist_ok=True)
        write_plan(cohort_path, selected_users[:user_count], as_of=as_of)
        cohort_plan_paths[str(user_count)] = str(cohort_path)
    seed_path.write_text(build_seed_sql(selected_users, as_of=as_of), encoding="utf-8")

    profile_counts = Counter(item.profile_type for item in selected_users)
    cohort_profile_counts = {
        str(user_count): dict(
            sorted(Counter(item.profile_type for item in selected_users[:user_count]).items())
        )
        for user_count in args.user_counts
    }
    cohort_dominant_genre_counts = {
        str(user_count): dict(
            sorted(
                Counter(
                    item.summary.dominant_genre
                    for item in selected_users[:user_count]
                    if item.summary.dominant_genre is not None
                ).items()
            )
        )
        for user_count in args.user_counts
    }
    candidate_counts = [item.candidate_count for item in selected_users]
    payload = {
        "format_version": PLAN_FORMAT_VERSION,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_as_of": as_of.isoformat(),
        "source": "MovieLens temporal holdout",
        "train_ratio": args.train_ratio,
        "rating_policy": {
            "saved": "5.0",
            "pinned": "3.5-4.5",
            "passed": "0.5-1.5",
            "ignored": "2.0-3.0",
            "holdout_relevant": ">=3.5",
        },
        "mapping": {
            "movielens_link_count": len(source_to_tmdb),
            "eligible_database_match_count": len(source_to_db),
        },
        "selection": {
            "eligible_user_count": len(summaries),
            "selected_user_count": len(selected_users),
            "nested_user_counts": list(args.user_counts),
            "anchor_evaluation_user_count": min(args.user_counts),
            "profile_policy": "focused users below the eligible population median genre entropy",
            "genre_distribution_policy": "round-robin by dominant positive-training genre",
            "profile_type_counts": dict(sorted(profile_counts.items())),
            "cohort_profile_type_counts": cohort_profile_counts,
            "cohort_dominant_genre_counts": cohort_dominant_genre_counts,
            "activity_median": thresholds.activity_median,
            "genre_entropy_median": thresholds.genre_entropy_median,
            "min_mapped_ratings": args.min_mapped_ratings,
            "min_train_positives": args.min_train_positives,
            "min_holdout_positives": args.min_holdout_positives,
        },
        "candidate_count": {
            "policy": "one recommendation candidate per rating in the temporal 30% holdout",
            "min": min(candidate_counts),
            "max": max(candidate_counts),
            "mean": sum(candidate_counts) / len(candidate_counts),
        },
        "outputs": {
            "plan": str(plan_path),
            "cohort_plans": cohort_plan_paths,
            "seed_sql": str(seed_path),
        },
    }
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def parse_user_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("user counts must be integers") from exc
    if not counts or any(count <= 0 for count in counts):
        raise argparse.ArgumentTypeError("user counts must be positive")
    if tuple(sorted(set(counts))) != counts:
        raise argparse.ArgumentTypeError("user counts must be unique and ascending")
    return counts


def load_database_movie_ids(db, tmdb_ids) -> dict[int, int]:
    values = sorted(set(int(value) for value in tmdb_ids))
    result: dict[int, int] = {}
    for start in range(0, len(values), 5_000):
        block = values[start : start + 5_000]
        statement = select(Movie.tmdb_id, Movie.id).where(
            Movie.tmdb_id.in_(block),
            *eligible_catalog_movie_clause(),
        )
        result.update(
            (int(tmdb_id), int(movie_id))
            for tmdb_id, movie_id in db.execute(statement)
            if tmdb_id is not None
        )
    return result


def normalize_as_of(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def write_plan(path: Path, users: list[SelectedUser], *, as_of: datetime) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for user in users:
            payload = {
                "format_version": PLAN_FORMAT_VERSION,
                "source_user_id": user.source_user_id,
                "email": evaluation_email(user.source_user_id),
                "profile_type": user.profile_type,
                "candidate_count": user.candidate_count,
                "evaluation_as_of": as_of.isoformat(),
                "summary": {
                    "mapped_rating_count": user.summary.mapped_rating_count,
                    "train_rating_count": user.summary.train_rating_count,
                    "train_positive_count": user.summary.train_positive_count,
                    "holdout_positive_count": user.summary.holdout_positive_count,
                    "genre_entropy": user.summary.genre_entropy,
                    "dominant_genre": user.summary.dominant_genre,
                },
                "train": [rating_payload(item) for item in user.train_ratings],
                "holdout": [rating_payload(item) for item in user.holdout_ratings],
            }
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def rating_payload(rating: MappedRating) -> dict[str, int | float]:
    return {
        "source_movie_id": rating.source_movie_id,
        "movie_id": rating.movie_id,
        "rating": rating.rating,
        "timestamp": rating.timestamp,
    }


def build_seed_sql(users: list[SelectedUser], *, as_of: datetime) -> str:
    source_rows = [
        {
            "source_user_id": user.source_user_id,
            "email": evaluation_email(user.source_user_id),
            "nickname": evaluation_nickname(user.source_user_id),
            "gender": "M" if user.source_user_id % 2 == 0 else "F",
        }
        for user in users
    ]
    rating_rows = []
    for user in users:
        normalized_times = normalized_training_times(user.train_ratings, as_of=as_of)
        for rating, occurred_at in zip(user.train_ratings, normalized_times, strict=True):
            if 2.0 <= rating.rating <= 3.0:
                continue
            rating_rows.append(
                {
                    "source_user_id": user.source_user_id,
                    "movie_id": rating.movie_id,
                    "rating": rating.rating,
                    "occurred_at": occurred_at.isoformat(),
                }
            )
    source_json = json.dumps(source_rows, separators=(",", ":"))
    ratings_json = json.dumps(rating_rows, separators=(",", ":"))
    return f"""\\set ON_ERROR_STOP on

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:long-term-eval'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test') THEN
        RAISE EXCEPTION 'V3 long-term evaluation users already exist; run 99_cleanup.sql first';
    END IF;
END $$;

CREATE TEMP TABLE _v3_eval_sources ON COMMIT DROP AS
SELECT *
FROM jsonb_to_recordset($v3eval${source_json}$v3eval$::jsonb)
    AS value(source_user_id integer, email text, nickname text, gender text);

INSERT INTO users (
    email, hashed_password, nickname, birth_date, gender, is_onboarding_completed
)
SELECT
    email,
    'v3-long-term-evaluation-only',
    nickname,
    DATE '1990-01-01',
    gender,
    false
FROM _v3_eval_sources
ORDER BY source_user_id;

CREATE TEMP TABLE _v3_eval_users ON COMMIT DROP AS
SELECT source.source_user_id, app_user.id AS user_id
FROM _v3_eval_sources AS source
JOIN users AS app_user ON app_user.email = source.email;

CREATE TEMP TABLE _v3_eval_ratings ON COMMIT DROP AS
SELECT value.source_user_id, value.movie_id, value.rating, value.occurred_at
FROM jsonb_to_recordset($v3eval${ratings_json}$v3eval$::jsonb)
    AS value(source_user_id integer, movie_id integer, rating double precision, occurred_at timestamptz);

CREATE TEMP TABLE _v3_eval_playlists (
    user_id integer PRIMARY KEY,
    playlist_id integer NOT NULL
) ON COMMIT DROP;

WITH inserted AS (
    INSERT INTO playlists (user_id, title, is_public)
    SELECT user_id, 'MovieLens saved', false
    FROM _v3_eval_users
    ORDER BY source_user_id
    RETURNING id, user_id
)
INSERT INTO _v3_eval_playlists (user_id, playlist_id)
SELECT user_id, id FROM inserted;

INSERT INTO playlist_movies (playlist_id, movie_id, created_at)
SELECT playlist.playlist_id, rating.movie_id, rating.occurred_at
FROM _v3_eval_ratings AS rating
JOIN _v3_eval_users AS app_user USING (source_user_id)
JOIN _v3_eval_playlists AS playlist USING (user_id)
WHERE rating.rating = 5.0;

INSERT INTO user_interactions (
    user_id, movie_id, is_pinned, is_watched, is_passed,
    pinned_at, watched_at, passed_at
)
SELECT
    app_user.user_id,
    rating.movie_id,
    rating.rating BETWEEN 3.5 AND 4.5,
    false,
    rating.rating <= 1.5,
    CASE WHEN rating.rating BETWEEN 3.5 AND 4.5 THEN rating.occurred_at END,
    NULL,
    CASE WHEN rating.rating <= 1.5 THEN rating.occurred_at END
FROM _v3_eval_ratings AS rating
JOIN _v3_eval_users AS app_user USING (source_user_id)
WHERE rating.rating BETWEEN 3.5 AND 4.5 OR rating.rating <= 1.5;

DO $$
DECLARE
    expected_users integer := {len(users)};
    actual_users integer;
BEGIN
    SELECT count(*) INTO actual_users FROM _v3_eval_users;
    IF actual_users <> expected_users THEN
        RAISE EXCEPTION 'V3 evaluation user count mismatch expected=% actual=%', expected_users, actual_users;
    END IF;
END $$;

COMMIT;

SELECT
    count(*) AS inserted_users,
    (SELECT count(*) FROM playlist_movies pm JOIN playlists p ON p.id = pm.playlist_id WHERE p.user_id IN (SELECT user_id FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test')) AS saved_rows,
    (SELECT count(*) FROM user_interactions ui JOIN users u ON u.id = ui.user_id WHERE u.email LIKE 'v3eval-ml-%@pinlm.test' AND ui.is_pinned) AS pinned_rows,
    (SELECT count(*) FROM user_interactions ui JOIN users u ON u.id = ui.user_id WHERE u.email LIKE 'v3eval-ml-%@pinlm.test' AND ui.is_passed) AS passed_rows
FROM users
WHERE email LIKE 'v3eval-ml-%@pinlm.test';
"""


def normalized_training_times(
    ratings: tuple[MappedRating, ...],
    *,
    as_of: datetime,
) -> tuple[datetime, ...]:
    if not ratings:
        return ()
    oldest = as_of - timedelta(days=365)
    newest = as_of - timedelta(days=31)
    if len(ratings) == 1:
        return (oldest,)
    span = newest - oldest
    return tuple(
        oldest + span * (index / (len(ratings) - 1))
        for index in range(len(ratings))
    )


def evaluation_email(source_user_id: int) -> str:
    return f"v3eval-ml-{source_user_id}@pinlm.test"


def evaluation_nickname(source_user_id: int) -> str:
    return f"v3e{source_user_id}"[:10]


if __name__ == "__main__":
    main()
