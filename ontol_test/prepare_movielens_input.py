import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare ontology test input from MovieLens CSV files.")
    parser.add_argument("--user-id", required=True, type=int, help="MovieLens userId")
    parser.add_argument("--ratings", default="ontol_test/inputs/ratings.csv")
    parser.add_argument("--links", default="ontol_test/inputs/links.csv")
    parser.add_argument("--movies", default="ontol_test/inputs/movies.csv")
    parser.add_argument("--output-dir", default="ontol_test/outputs")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pin-min", type=float, default=3.5)
    parser.add_argument("--pass-max", type=float, default=1.5)
    parser.add_argument("--saved-rating", type=float, default=5.0)
    parser.add_argument(
        "--neutral-action",
        choices=["watched", "ignore"],
        default="ignore",
        help="How to handle ratings between pass-max and pin-min",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--subscribed-only", action="store_true")
    args = parser.parse_args()

    prepared = prepare_input(
        user_id=args.user_id,
        ratings_path=Path(args.ratings),
        links_path=Path(args.links),
        movies_path=Path(args.movies),
        sample_size=args.sample_size,
        train_ratio=args.train_ratio,
        seed=args.seed,
        pin_min=args.pin_min,
        pass_max=args.pass_max,
        saved_rating=args.saved_rating,
        neutral_action=args.neutral_action,
        limit=args.limit,
        subscribed_only=args.subscribed_only,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"user_{args.user_id}_seed_{args.seed}"
    write_json(output_dir / f"{prefix}_input.json", prepared["input"])
    write_json(output_dir / f"{prefix}_holdout.json", prepared["holdout"])
    write_json(output_dir / f"{prefix}_missing.json", prepared["missing"])
    write_json(output_dir / f"{prefix}_summary.json", prepared["summary"])
    print(
        "prepared "
        f"user_id={args.user_id} "
        f"train={prepared['summary']['train_count']} "
        f"holdout={prepared['summary']['holdout_count']} "
        f"missing={prepared['summary']['missing_count']}"
    )


def prepare_input(
    *,
    user_id: int,
    ratings_path: Path,
    links_path: Path,
    movies_path: Path,
    sample_size: int,
    train_ratio: float,
    seed: int,
    pin_min: float,
    pass_max: float,
    saved_rating: float,
    neutral_action: str,
    limit: int,
    subscribed_only: bool,
) -> dict[str, Any]:
    if not 0 < train_ratio < 1:
        raise ValueError("train-ratio must be between 0 and 1")
    if sample_size <= 0:
        raise ValueError("sample-size must be positive")

    movie_meta = load_movie_meta(movies_path)
    links = load_links(links_path)
    ratings = load_user_ratings(ratings_path, user_id=user_id)
    if not ratings:
        raise ValueError(f"userId={user_id} has no ratings")

    rng = random.Random(seed)
    rng.shuffle(ratings)
    sampled = ratings[:sample_size]
    mapped, missing = map_ratings_to_db_movies(sampled, links=links, movie_meta=movie_meta)

    rng.shuffle(mapped)
    train_count = int(len(mapped) * train_ratio)
    train_items = mapped[:train_count]
    holdout_items = mapped[train_count:]

    input_payload = build_recommend_input(
        source_user_id=user_id,
        train_items=train_items,
        seed=seed,
        train_ratio=train_ratio,
        pin_min=pin_min,
        pass_max=pass_max,
        saved_rating=saved_rating,
        neutral_action=neutral_action,
        limit=limit,
        subscribed_only=subscribed_only,
    )

    return {
        "input": input_payload,
        "holdout": {
            "source_user_id": user_id,
            "seed": seed,
            "items": holdout_items,
        },
        "missing": {
            "source_user_id": user_id,
            "seed": seed,
            "items": missing,
        },
        "summary": {
            "source_user_id": user_id,
            "seed": seed,
            "sample_size_requested": sample_size,
            "raw_rating_count": len(ratings),
            "sampled_count": len(sampled),
            "mapped_count": len(mapped),
            "missing_count": len(missing),
            "train_count": len(train_items),
            "holdout_count": len(holdout_items),
            "pin_min": pin_min,
            "pass_max": pass_max,
            "saved_rating": saved_rating,
            "neutral_action": neutral_action,
        },
    }


def load_movie_meta(path: Path) -> dict[int, dict[str, Any]]:
    meta: dict[int, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            movie_id = parse_int(row.get("movieId"))
            if movie_id is None:
                continue
            meta[movie_id] = {
                "movieId": movie_id,
                "title": row.get("title") or "",
                "genres": row.get("genres") or "",
            }
    return meta


def load_links(path: Path) -> dict[int, dict[str, Any]]:
    links: dict[int, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            movie_id = parse_int(row.get("movieId"))
            if movie_id is None:
                continue
            links[movie_id] = {
                "movieId": movie_id,
                "imdbId": row.get("imdbId") or "",
                "tmdbId": parse_int(row.get("tmdbId")),
            }
    return links


def load_user_ratings(path: Path, *, user_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            current_user_id = parse_int(row.get("userId"))
            if current_user_id != user_id:
                continue
            movie_id = parse_int(row.get("movieId"))
            rating = parse_float(row.get("rating"))
            if movie_id is None or rating is None:
                continue
            rows.append(
                {
                    "userId": current_user_id,
                    "movieId": movie_id,
                    "rating": rating,
                    "timestamp": parse_int(row.get("timestamp")),
                }
            )
    return rows


def map_ratings_to_db_movies(
    ratings: list[dict[str, Any]],
    *,
    links: dict[int, dict[str, Any]],
    movie_meta: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tmdb_ids = sorted(
        {
            links[item["movieId"]]["tmdbId"]
            for item in ratings
            if item["movieId"] in links and links[item["movieId"]]["tmdbId"] is not None
        }
    )
    db_movie_by_tmdb_id = load_db_movie_map(tmdb_ids)
    mapped: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for item in ratings:
        movie_id = int(item["movieId"])
        link = links.get(movie_id)
        meta = movie_meta.get(movie_id, {})
        tmdb_id = link.get("tmdbId") if link else None
        base = {
            "movieId": movie_id,
            "tmdbId": tmdb_id,
            "rating": float(item["rating"]),
            "timestamp": item.get("timestamp"),
            "title": meta.get("title") or "",
            "genres": meta.get("genres") or "",
        }
        if tmdb_id is None:
            missing.append({**base, "missing_reason": "missing_tmdb_id"})
            continue
        db_movie = db_movie_by_tmdb_id.get(int(tmdb_id))
        if not db_movie:
            missing.append({**base, "missing_reason": "tmdb_id_not_found_in_db"})
            continue
        mapped.append({**base, **db_movie})
    return mapped, missing


def load_db_movie_map(tmdb_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not tmdb_ids:
        return {}
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, tmdb_id, title, title_ko, popularity, vote_average
                FROM movies
                WHERE tmdb_id = ANY(:tmdb_ids)
                """
            ),
            {"tmdb_ids": tmdb_ids},
        )
        return {
            int(row.tmdb_id): {
                "db_movie_id": int(row.id),
                "db_title": row.title,
                "db_title_ko": row.title_ko,
                "db_popularity": float(row.popularity or 0.0),
                "db_vote_average": float(row.vote_average or 0.0),
            }
            for row in rows
            if row.tmdb_id is not None
        }
    finally:
        db.close()


def build_recommend_input(
    *,
    source_user_id: int,
    train_items: list[dict[str, Any]],
    seed: int,
    train_ratio: float,
    pin_min: float,
    pass_max: float,
    saved_rating: float,
    neutral_action: str,
    limit: int,
    subscribed_only: bool,
) -> dict[str, Any]:
    pinned_movie_ids: list[int] = []
    passed_movie_ids: list[int] = []
    watched_movie_ids: list[int] = []
    saved_movie_ids: list[int] = []
    ignored_movie_ids: list[int] = []

    for item in train_items:
        db_movie_id = int(item["db_movie_id"])
        rating = float(item["rating"])
        if rating == saved_rating:
            saved_movie_ids.append(db_movie_id)
        elif rating >= pin_min:
            pinned_movie_ids.append(db_movie_id)
        elif rating <= pass_max:
            passed_movie_ids.append(db_movie_id)
        elif neutral_action == "watched":
            watched_movie_ids.append(db_movie_id)
        else:
            ignored_movie_ids.append(db_movie_id)

    return {
        "source_user_id": source_user_id,
        "split": {
            "seed": seed,
            "train_ratio": train_ratio,
        },
        "rating_policy": {
            "saved_rating": saved_rating,
            "pin_min": pin_min,
            "pass_max": pass_max,
            "neutral_action": neutral_action,
        },
        "preferred_genre_ids": [],
        "favorite_movie_ids": sorted(set(saved_movie_ids)),
        "saved_movie_ids": sorted(set(saved_movie_ids)),
        "pinned_movie_ids": sorted(set(pinned_movie_ids)),
        "passed_movie_ids": sorted(set(passed_movie_ids)),
        "watched_movie_ids": sorted(set(watched_movie_ids)),
        "ignored_movie_ids": sorted(set(ignored_movie_ids)),
        "subscribed_ott_ids": [],
        "limit": limit,
        "subscribed_only": subscribed_only,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
