from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.genre import Genre
from app.models.mapping import movie_genres
from app.models.movie import Movie
from app.services.recsys.v3.retrieval.score_calibration import (
    collaborative_adjusted_user_representations,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact


ROOT = Path("tests/v3_long_term_eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Explain one MovieLens user's V3 long-term evaluation result"
    )
    parser.add_argument("source_user_id", type=int)
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "generated/evaluation_plan.jsonl",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=ROOT / "outputs/long_term_evaluation_results.jsonl",
    )
    parser.add_argument(
        "--movies",
        type=Path,
        default=ROOT / "inputs/movies.csv",
    )
    parser.add_argument(
        "--links",
        type=Path,
        default=ROOT / "inputs/links.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Optional hybrid artifact used to attach exact scores and feature counts",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = load_jsonl_record(args.plan, args.source_user_id)
    result = load_jsonl_record(args.results, args.source_user_id)
    movie_lens = load_movielens_movies(args.movies)
    source_by_tmdb = load_source_by_tmdb(args.links)

    train_positive = [item for item in plan["train"] if float(item["rating"]) >= 3.5]
    holdout_positive = [
        item for item in plan["holdout"] if float(item["rating"]) >= 3.5
    ]
    model_ranking = [int(value) for value in result["retrieval"]["ranked_movie_ids"]]
    final_ranking = [int(value) for value in result["final"]["ranked_movie_ids"]]
    all_candidate_ids = set(model_ranking) | set(final_ranking)
    metadata = load_db_metadata(all_candidate_ids)
    enriched = enrich_metadata(metadata, source_by_tmdb, movie_lens)
    if args.artifact is not None:
        annotate_model_scores(
            enriched,
            artifact_path=args.artifact,
            user_id=int(result["user_id"]),
            collaborative_confidence=float(
                result["final"]["retrieval_diagnostics"][
                    "collaborative_effective_confidence"
                ]
            ),
        )

    train_genres = genre_distribution(train_positive, movie_lens)
    holdout_genres = genre_distribution(holdout_positive, movie_lens)
    dominant_genres = tuple(genre for genre, _count in train_genres.most_common(5))
    holdout_by_id = {int(item["movie_id"]): item for item in holdout_positive}

    model_hits = hit_rows(model_ranking, holdout_by_id, movie_lens, enriched)
    final_hits = hit_rows(final_ranking, holdout_by_id, movie_lens, enriched)
    model_top = ranking_rows(
        model_ranking[:30], holdout_by_id, enriched, dominant_genres
    )
    final_top = ranking_rows(
        final_ranking[:30], holdout_by_id, enriched, dominant_genres
    )

    report = {
        "source_user_id": args.source_user_id,
        "database_user_id": int(result["user_id"]),
        "profile_type": result["profile_type"],
        "counts": {
            "train_ratings": len(plan["train"]),
            "train_positive": len(train_positive),
            "holdout_positive": len(holdout_positive),
            "model_candidates": len(model_ranking),
            "final_candidates": len(final_ranking),
        },
        "train_positive_genres": distribution_rows(train_genres, len(train_positive)),
        "holdout_positive_genres": distribution_rows(
            holdout_genres, len(holdout_positive)
        ),
        "dominant_train_genres": dominant_genres,
        "model": {
            "metrics": result["retrieval"],
            "genre_alignment": alignment_summary(
                model_ranking, enriched, dominant_genres
            ),
            "top_30": model_top,
            "holdout_hits": model_hits,
        },
        "final": {
            "metrics": {
                key: value
                for key, value in result["final"].items()
                if key not in {"ranked_movie_ids"}
            },
            "genre_alignment": alignment_summary(
                final_ranking, enriched, dominant_genres
            ),
            "top_30": final_top,
            "holdout_hits": final_hits,
        },
        "final_effect": {
            "model_top_100_holdout_hits": sorted(
                set(model_ranking[:100]) & set(holdout_by_id)
            ),
            "final_top_100_holdout_hits": sorted(
                set(final_ranking[:100]) & set(holdout_by_id)
            ),
            "model_top_100_hits_lost_by_final": sorted(
                (set(model_ranking[:100]) & set(holdout_by_id))
                - set(final_ranking[:100])
            ),
        },
    }

    output = args.output or (
        ROOT / "outputs" / f"single_user_{args.source_user_id}_analysis.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", "output": str(output)}, sort_keys=True))


def load_jsonl_record(path: Path, source_user_id: int) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if int(row["source_user_id"]) == source_user_id:
                return row
    raise SystemExit(f"source_user_id={source_user_id} is missing from {path}")


def load_movielens_movies(path: Path) -> dict[int, dict]:
    result = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result[int(row["movieId"])] = {
                "title": row["title"],
                "genres": tuple(
                    value
                    for value in row["genres"].split("|")
                    if value and value != "(no genres listed)"
                ),
            }
    return result


def load_source_by_tmdb(path: Path) -> dict[int, int]:
    result = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tmdb_id = (row.get("tmdbId") or "").strip()
            if tmdb_id:
                result[int(float(tmdb_id))] = int(row["movieId"])
    return result


def load_db_metadata(movie_ids: Iterable[int]) -> dict[int, dict]:
    ids = sorted(set(int(value) for value in movie_ids))
    result = {
        movie_id: {
            "movie_id": movie_id,
            "tmdb_id": None,
            "title": None,
            "vote_average": None,
            "vote_count": None,
            "release_date": None,
            "genres": [],
        }
        for movie_id in ids
    }
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Movie.id,
                Movie.tmdb_id,
                Movie.title,
                Movie.vote_average,
                Movie.vote_count,
                Movie.release_date,
                Genre.name,
            )
            .select_from(Movie)
            .outerjoin(movie_genres, movie_genres.c.movie_id == Movie.id)
            .outerjoin(Genre, Genre.id == movie_genres.c.genre_id)
            .where(Movie.id.in_(ids))
        ).all()
    for movie_id, tmdb_id, title, vote_average, vote_count, release_date, genre in rows:
        item = result[int(movie_id)]
        item["tmdb_id"] = int(tmdb_id) if tmdb_id is not None else None
        item["title"] = title
        item["vote_average"] = float(vote_average) if vote_average is not None else None
        item["vote_count"] = int(vote_count) if vote_count is not None else None
        item["release_date"] = release_date.isoformat() if release_date is not None else None
        if genre:
            item["genres"].append(str(genre))
    return result


def enrich_metadata(
    metadata: dict[int, dict],
    source_by_tmdb: dict[int, int],
    movie_lens: dict[int, dict],
) -> dict[int, dict]:
    for item in metadata.values():
        source_id = source_by_tmdb.get(item["tmdb_id"])
        source = movie_lens.get(source_id) if source_id is not None else None
        item["source_movie_id"] = source_id
        item["movielens_title"] = source["title"] if source else None
        if source and source["genres"]:
            item["genres"] = list(source["genres"])
    return metadata


def annotate_model_scores(
    metadata: dict[int, dict],
    *,
    artifact_path: Path,
    user_id: int,
    collaborative_confidence: float,
) -> None:
    artifact = load_runtime_hybrid_artifact(artifact_path)
    user_index = artifact.user_index(user_id)
    if user_index is None:
        raise SystemExit(f"user_id={user_id} is missing from {artifact_path}")
    user_features = artifact.user_features[user_index : user_index + 1]
    user_biases, user_embeddings = collaborative_adjusted_user_representations(
        artifact.model,
        user_features,
        identity_feature_count=len(artifact.user_ids),
        collaborative_confidences=np.asarray(
            [collaborative_confidence], dtype=np.float32
        ),
        centering_weight=artifact.known_user_score_centering_weight,
        component_means=artifact.user_representation_component_means,
    )
    for movie_id, item in metadata.items():
        movie_index = artifact.movie_index(movie_id)
        if movie_index is None:
            continue
        features = artifact.item_features[movie_index : movie_index + 1]
        item_biases, item_embeddings = artifact.model.get_item_representations(features)
        score = float(
            np.asarray(user_embeddings, dtype=np.float32)[0]
            @ np.asarray(item_embeddings, dtype=np.float32)[0]
            + np.asarray(user_biases, dtype=np.float32)[0]
            + (1.0 - artifact.known_user_score_centering_weight)
            * np.asarray(item_biases, dtype=np.float32)[0]
        )
        item["model_score"] = score
        item["item_feature_count"] = int(features.nnz)
        item["semantic_feature_count"] = int(
            np.count_nonzero(features.indices >= len(artifact.movie_ids))
        )
        semantic_indices = features.indices[
            features.indices >= len(artifact.movie_ids)
        ]
        if len(semantic_indices) <= 10:
            item["semantic_features"] = [
                artifact.item_feature_tokens[int(index)]
                for index in semantic_indices
            ]


def genre_distribution(ratings: list[dict], movie_lens: dict[int, dict]) -> Counter:
    result: Counter[str] = Counter()
    for rating in ratings:
        genres = movie_lens.get(int(rating["source_movie_id"]), {}).get("genres", ())
        result.update(genres)
    return result


def distribution_rows(distribution: Counter, movie_count: int) -> list[dict]:
    return [
        {
            "genre": genre,
            "movie_count": count,
            "movie_share": round(count / movie_count, 4) if movie_count else 0.0,
        }
        for genre, count in distribution.most_common()
    ]


def alignment_summary(
    ranking: list[int], metadata: dict[int, dict], dominant_genres: tuple[str, ...]
) -> dict[str, dict]:
    dominant = set(dominant_genres)
    result = {}
    for cutoff in (10, 20, 50, 100, len(ranking)):
        rows = [metadata[movie_id] for movie_id in ranking[:cutoff]]
        linked = sum(item["source_movie_id"] is not None for item in rows)
        aligned = sum(bool(dominant & set(item["genres"])) for item in rows)
        result[str(cutoff)] = {
            "count": len(rows),
            "movielens_linked": linked,
            "dominant_genre_matches": aligned,
            "dominant_genre_match_rate": round(aligned / len(rows), 4) if rows else 0.0,
        }
    return result


def ranking_rows(
    ranking: list[int],
    holdout_by_id: dict[int, dict],
    metadata: dict[int, dict],
    dominant_genres: tuple[str, ...],
) -> list[dict]:
    dominant = set(dominant_genres)
    rows = []
    for rank, movie_id in enumerate(ranking, start=1):
        item = metadata[movie_id]
        holdout = holdout_by_id.get(movie_id)
        rows.append(
            {
                "rank": rank,
                "movie_id": movie_id,
                "tmdb_id": item["tmdb_id"],
                "title": item["movielens_title"] or item["title"],
                "genres": item["genres"],
                "dominant_genre_match": bool(dominant & set(item["genres"])),
                "holdout_rating": float(holdout["rating"]) if holdout else None,
                "vote_average": item["vote_average"],
                "vote_count": item["vote_count"],
                "model_score": item.get("model_score"),
                "item_feature_count": item.get("item_feature_count"),
                "semantic_feature_count": item.get("semantic_feature_count"),
                "semantic_features": item.get("semantic_features"),
            }
        )
    return rows


def hit_rows(
    ranking: list[int],
    holdout_by_id: dict[int, dict],
    movie_lens: dict[int, dict],
    metadata: dict[int, dict],
) -> list[dict]:
    rows = []
    for rank, movie_id in enumerate(ranking, start=1):
        holdout = holdout_by_id.get(movie_id)
        if holdout is None:
            continue
        source_movie_id = int(holdout["source_movie_id"])
        source = movie_lens.get(source_movie_id, {})
        rows.append(
            {
                "rank": rank,
                "movie_id": movie_id,
                "source_movie_id": source_movie_id,
                "tmdb_id": metadata[movie_id]["tmdb_id"],
                "title": source.get("title") or metadata[movie_id]["title"],
                "genres": source.get("genres", metadata[movie_id]["genres"]),
                "rating": float(holdout["rating"]),
            }
        )
    return rows


if __name__ == "__main__":
    main()
