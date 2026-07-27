import argparse
import csv
import gzip
import json
import time
from pathlib import Path
from typing import Any, Iterable, TextIO

from sqlalchemy import text

from app.crud.recsys.ontology import get_active_build
from app.db.session import SessionLocal
from app.services.recsys.v2.candidate_generator import (
    build_feature_values_sql,
    build_profile_feature_rows,
    normalize_popularity,
    normalize_rating,
)
from app.services.recsys.v2.ranker import rank_candidates
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile
from app.services.recsys.v2.scorer import score_candidates
from ontol_test.prepare_movielens_input import (
    build_recommend_input,
    load_links,
    load_movie_meta,
    parse_float,
    parse_int,
)
from ontol_test.run_ontology_recommend import build_profile_from_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ontology recommender with MovieLens ratings.")
    parser.add_argument("--ratings", default="ontol_test/inputs/ratings.csv")
    parser.add_argument("--links", default="ontol_test/inputs/links.csv")
    parser.add_argument("--movies", default="ontol_test/inputs/movies.csv")
    parser.add_argument("--output-dir", default="ontol_test/outputs")
    parser.add_argument("--min-ratings", type=int, default=100)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--pin-min", type=float, default=3.5)
    parser.add_argument("--pass-max", type=float, default=1.5)
    parser.add_argument("--saved-rating", type=float, default=5.0)
    parser.add_argument("--neutral-action", choices=["watched", "ignore"], default="watched")
    parser.add_argument("--seed", type=int, default=42, help="Kept for compatibility; timestamp split does not use it")
    parser.add_argument("--max-users", type=int, default=None, help="Optional smoke-test cap")
    parser.add_argument("--start-user-id", type=int, default=None)
    parser.add_argument("--end-user-id", type=int, default=None)
    parser.add_argument("--user-ids", default=None, help="Comma-separated userId allowlist")
    parser.add_argument("--min-mapped-ratings", type=int, default=30)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--gzip-output", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl.gz" if args.gzip_output else ".jsonl"
    results_path = output_dir / f"evaluation_results{suffix}"
    skipped_path = output_dir / f"skipped_users{suffix}"
    summary_path = output_dir / "evaluation_run_summary.json"
    selected_user_ids = parse_user_ids(args.user_ids)

    started_at = time.time()
    links = load_links(Path(args.links))
    movie_meta = load_movie_meta(Path(args.movies))
    db_movie_by_tmdb_id = load_db_movie_map_for_links(links)

    counters = {
        "total_user_count": 0,
        "evaluated_user_count": 0,
        "skipped_user_count": 0,
        "total_rating_count": 0,
        "total_used_movie_count": 0,
        "total_missing_count": 0,
        "total_candidate_count": 0,
        "total_rated_candidate_count": 0,
    }

    db = SessionLocal()
    try:
        results_file = open_output(results_path, gzip_output=args.gzip_output)
        skipped_file = open_output(skipped_path, gzip_output=args.gzip_output)
        try:
            run_loop(
                db=db,
                ratings_path=Path(args.ratings),
                links=links,
                movie_meta=movie_meta,
                db_movie_by_tmdb_id=db_movie_by_tmdb_id,
                results_file=results_file,
                skipped_file=skipped_file,
                counters=counters,
                min_ratings=args.min_ratings,
                min_mapped_ratings=args.min_mapped_ratings,
                train_ratio=args.train_ratio,
                seed=args.seed,
                limit=args.limit,
                pin_min=args.pin_min,
                pass_max=args.pass_max,
                saved_rating=args.saved_rating,
                neutral_action=args.neutral_action,
                max_users=args.max_users,
                start_user_id=args.start_user_id,
                end_user_id=args.end_user_id,
                selected_user_ids=selected_user_ids,
                progress_every=args.progress_every,
                started_at=started_at,
            )
        finally:
            results_file.close()
            skipped_file.close()
    finally:
        db.close()

    elapsed_seconds = round(time.time() - started_at, 3)
    run_summary = {
        **counters,
        "min_ratings": args.min_ratings,
        "min_mapped_ratings": args.min_mapped_ratings,
        "train_ratio": args.train_ratio,
        "candidate_scope": "all_holdout",
        "limit_arg": args.limit,
        "user_filters": {
            "start_user_id": args.start_user_id,
            "end_user_id": args.end_user_id,
            "user_ids": sorted(selected_user_ids) if selected_user_ids else None,
            "max_users": args.max_users,
        },
        "rating_policy": {
            "pass": "0.5~1.5",
            "neutral": "2.0~3.0",
            "pin": "3.5~4.5",
            "saved": "5.0",
            "pin_min": args.pin_min,
            "pass_max": args.pass_max,
            "saved_rating": args.saved_rating,
            "neutral_action": args.neutral_action,
        },
        "elapsed_seconds": elapsed_seconds,
        "results_path": str(results_path),
        "skipped_path": str(skipped_path),
    }
    summary_path.write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "evaluated "
        f"users={counters['evaluated_user_count']} "
        f"skipped={counters['skipped_user_count']} "
        f"results={results_path} "
        f"summary={summary_path}"
    )


def run_loop(
    *,
    db,
    ratings_path: Path,
    links: dict[int, dict[str, Any]],
    movie_meta: dict[int, dict[str, Any]],
    db_movie_by_tmdb_id: dict[int, dict[str, Any]],
    results_file: TextIO,
    skipped_file: TextIO,
    counters: dict[str, int],
    min_ratings: int,
    min_mapped_ratings: int,
    train_ratio: float,
    seed: int,
    limit: int,
    pin_min: float,
    pass_max: float,
    saved_rating: float,
    neutral_action: str,
    max_users: int | None,
    start_user_id: int | None,
    end_user_id: int | None,
    selected_user_ids: set[int] | None,
    progress_every: int,
    started_at: float,
) -> None:
    for user_id, ratings in iter_user_ratings(ratings_path):
        if selected_user_ids is not None and user_id not in selected_user_ids:
            continue
        if start_user_id is not None and user_id < start_user_id:
            continue
        if end_user_id is not None and user_id > end_user_id:
            break

        counters["total_user_count"] += 1
        counters["total_rating_count"] += len(ratings)

        if len(ratings) < min_ratings:
            counters["skipped_user_count"] += 1
            write_jsonl(
                skipped_file,
                {
                    "user_id": user_id,
                    "rating_count": len(ratings),
                    "reason": "ratings_below_minimum",
                },
            )
            continue

        mapped, missing = map_ratings_to_db_movies(ratings, links, movie_meta, db_movie_by_tmdb_id)
        if len(mapped) < min_mapped_ratings:
            counters["skipped_user_count"] += 1
            write_jsonl(
                skipped_file,
                {
                    "user_id": user_id,
                    "rating_count": len(ratings),
                    "mapped_count": len(mapped),
                    "missing_count": len(missing),
                    "reason": "mapped_ratings_below_minimum",
                },
            )
            continue

        user_result = evaluate_user(
            db=db,
            user_id=user_id,
            total_rating_count=len(ratings),
            mapped=mapped,
            missing=missing,
            train_ratio=train_ratio,
            seed=seed,
            limit=limit,
            pin_min=pin_min,
            pass_max=pass_max,
            saved_rating=saved_rating,
            neutral_action=neutral_action,
        )
        write_jsonl(results_file, user_result)

        summary = user_result["summary"]
        counters["evaluated_user_count"] += 1
        counters["total_used_movie_count"] += user_result["used_movie_count"]
        counters["total_missing_count"] += summary["missing_count"]
        counters["total_candidate_count"] += summary["candidate_count"]
        counters["total_rated_candidate_count"] += summary["rated_candidate_count"]

        if progress_every > 0 and counters["evaluated_user_count"] % progress_every == 0:
            elapsed = max(time.time() - started_at, 0.001)
            rate = counters["evaluated_user_count"] / elapsed
            print(
                "progress "
                f"evaluated={counters['evaluated_user_count']} "
                f"skipped={counters['skipped_user_count']} "
                f"current_user={user_id} "
                f"rate={rate:.2f}/s",
                flush=True,
            )

        if max_users is not None and counters["evaluated_user_count"] >= max_users:
            break


def evaluate_user(
    *,
    db,
    user_id: int,
    total_rating_count: int,
    mapped: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    train_ratio: float,
    seed: int,
    limit: int,
    pin_min: float,
    pass_max: float,
    saved_rating: float,
    neutral_action: str,
) -> dict[str, Any]:
    split_items = deterministic_split(mapped, train_ratio=train_ratio, seed=seed, user_id=user_id)
    train_items = split_items["train"]
    holdout_items = split_items["holdout"]
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
        subscribed_only=False,
    )
    ranked_holdout = rank_holdout_candidates(
        db,
        payload=input_payload,
        holdout_items=holdout_items,
    )
    holdout_by_movie_id = {int(item["db_movie_id"]): item for item in holdout_items}

    candidate: list[list[int | float]] = []
    for candidate_score in ranked_holdout:
        holdout = holdout_by_movie_id.get(candidate_score.movie_id)
        if holdout:
            candidate.append(to_tmdb_rating_pair(holdout))

    rated_movies_in_candidates: list[dict[str, Any]] = []
    for rank, candidate_score in enumerate(ranked_holdout, start=1):
        holdout = holdout_by_movie_id.get(candidate_score.movie_id)
        if holdout:
            rated_movies_in_candidates.append(candidate_to_evaluation_item(rank, candidate_score, holdout))

    return {
        "user_id": user_id,
        "used_movie_count": len(mapped),
        "total_count": total_rating_count,
        "candidate": candidate,
        "summary": {
            "candidate_count": len(rated_movies_in_candidates),
            "rated_candidate_count": len(rated_movies_in_candidates),
            "holdout_ranked_count": len(ranked_holdout),
            "missing_count": len(missing),
            "train_count": len(train_items),
            "holdout_count": len(holdout_items),
        },
    }


def rank_holdout_candidates(
    db,
    *,
    payload: dict[str, Any],
    holdout_items: list[dict[str, Any]],
) -> list[CandidateScore]:
    profile = build_profile_from_payload(db, payload)
    session_profile = SessionProfile(feed_session_key="movielens_holdout_eval")
    holdout_movie_ids = sorted({int(item["db_movie_id"]) for item in holdout_items})
    if not holdout_movie_ids:
        return []

    active_build = get_active_build(db)
    feature_rows = build_profile_feature_rows(profile)
    if active_build is None or not feature_rows:
        candidates = build_quality_only_holdout_candidates(db, holdout_movie_ids)
    else:
        candidates = build_graph_holdout_candidates(
            db,
            build_id=int(active_build.id),
            feature_rows=feature_rows,
            candidate_movie_ids=holdout_movie_ids,
        )

    scored = score_candidates(candidates, profile=profile, session_profile=session_profile)
    return rank_candidates(scored)


def build_graph_holdout_candidates(
    db,
    *,
    build_id: int,
    feature_rows: list[tuple[str, str, float]],
    candidate_movie_ids: list[int],
) -> list[CandidateScore]:
    values_sql, params = build_feature_values_sql(feature_rows)
    rows = db.execute(
        text(
            f"""
            WITH profile_feature(node_type, ref_id, profile_score) AS (
                VALUES {values_sql}
            ),
            graph_matches AS (
                SELECT
                    movie_node.ref_id::integer AS movie_id,
                    sum(edge.weight * edge.confidence * profile_feature.profile_score) AS graph_score,
                    count(DISTINCT feature_node.id) AS matched_feature_count,
                    json_object_agg(
                        profile_feature.node_type || ':' || profile_feature.ref_id,
                        edge.weight * edge.confidence * profile_feature.profile_score
                    ) AS matched_features
                FROM profile_feature
                JOIN ontology_nodes feature_node
                    ON feature_node.build_id = :build_id
                    AND feature_node.node_type = profile_feature.node_type
                    AND feature_node.ref_id = profile_feature.ref_id
                JOIN ontology_edges edge
                    ON edge.build_id = :build_id
                    AND edge.target_node_id = feature_node.id
                    AND edge.relation_type IN (
                        'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                    )
                JOIN ontology_nodes movie_node
                    ON movie_node.id = edge.source_node_id
                    AND movie_node.node_type = 'movie'
                WHERE movie_node.node_type = 'movie'
                  AND movie_node.ref_id::integer = ANY(:candidate_movie_ids)
                GROUP BY movie_node.ref_id
            )
            SELECT
                movie.id AS movie_id,
                COALESCE(graph_matches.graph_score, 0.0) AS graph_score,
                movie.popularity,
                movie.vote_average,
                COALESCE(graph_matches.matched_feature_count, 0) AS matched_feature_count,
                COALESCE(graph_matches.matched_features, '{{}}'::json) AS matched_features
            FROM movies movie
            LEFT JOIN graph_matches
                ON graph_matches.movie_id = movie.id
            WHERE movie.id = ANY(:candidate_movie_ids)
              AND movie.adult IS FALSE
            """
        ),
        {
            **params,
            "build_id": build_id,
            "candidate_movie_ids": candidate_movie_ids,
        },
    )
    candidates: list[CandidateScore] = []
    for row in rows:
        popularity_score = normalize_popularity(row.popularity)
        rating_score = normalize_rating(row.vote_average)
        graph_score = float(row.graph_score or 0.0)
        candidates.append(
            CandidateScore(
                movie_id=int(row.movie_id),
                score=graph_score + popularity_score + rating_score,
                source="ontology_holdout_rerank",
                source_scores={
                    "graph": graph_score,
                    "popularity": popularity_score,
                    "rating": rating_score,
                },
                explanation_tags=list((row.matched_features or {}).keys())[:8],
                metadata={
                    "matched_feature_count": int(row.matched_feature_count or 0),
                    "candidate_pool": "holdout",
                },
            )
        )
    return candidates


def build_quality_only_holdout_candidates(db, candidate_movie_ids: list[int]) -> list[CandidateScore]:
    rows = db.execute(
        text(
            """
            SELECT id, popularity, vote_average
            FROM movies
            WHERE id = ANY(:candidate_movie_ids)
              AND adult IS FALSE
            """
        ),
        {"candidate_movie_ids": candidate_movie_ids},
    )
    return [
        CandidateScore(
            movie_id=int(row.id),
            score=normalize_popularity(row.popularity) + normalize_rating(row.vote_average),
            source="quality_holdout_rerank",
            source_scores={
                "popularity": normalize_popularity(row.popularity),
                "rating": normalize_rating(row.vote_average),
            },
            explanation_tags=["quality"],
            metadata={"candidate_pool": "holdout"},
        )
        for row in rows
    ]


def candidate_to_evaluation_item(
    rank: int,
    candidate: CandidateScore,
    holdout: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rank": rank,
        "movieId": holdout["movieId"],
        "tmdbId": holdout["tmdbId"],
        "db_movie_id": holdout["db_movie_id"],
        "rating": holdout["rating"],
        "title": holdout["title"],
        "score": candidate.score,
        "source": candidate.source,
        "source_scores": candidate.source_scores,
        "explanation_tags": candidate.explanation_tags,
        "metadata": candidate.metadata,
    }


def to_tmdb_rating_pair(item: dict[str, Any]) -> list[int | float]:
    rating = float(item["rating"])
    if rating.is_integer():
        rating = int(rating)
    return [int(item["tmdbId"]), rating]


def deterministic_split(
    items: list[dict[str, Any]],
    *,
    train_ratio: float,
    seed: int,
    user_id: int,
) -> dict[str, list[dict[str, Any]]]:
    del seed, user_id
    ordered = sorted(
        items,
        key=lambda item: (
            item.get("timestamp") is None,
            int(item.get("timestamp") or 0),
            int(item["movieId"]),
        ),
    )
    train_count = int(len(ordered) * train_ratio)
    return {
        "train": ordered[:train_count],
        "holdout": ordered[train_count:],
    }


def map_ratings_to_db_movies(
    ratings: list[dict[str, Any]],
    links: dict[int, dict[str, Any]],
    movie_meta: dict[int, dict[str, Any]],
    db_movie_by_tmdb_id: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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


def load_db_movie_map_for_links(links: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    tmdb_ids = sorted({int(item["tmdbId"]) for item in links.values() if item.get("tmdbId") is not None})
    db = SessionLocal()
    try:
        result: dict[int, dict[str, Any]] = {}
        for chunk in chunks(tmdb_ids, 5000):
            rows = db.execute(
                text(
                    """
                    SELECT id, tmdb_id, title, title_ko, popularity, vote_average
                    FROM movies
                    WHERE tmdb_id = ANY(:tmdb_ids)
                    """
                ),
                {"tmdb_ids": chunk},
            )
            for row in rows:
                if row.tmdb_id is None:
                    continue
                result[int(row.tmdb_id)] = {
                    "db_movie_id": int(row.id),
                    "db_title": row.title,
                    "db_title_ko": row.title_ko,
                    "db_popularity": float(row.popularity or 0.0),
                    "db_vote_average": float(row.vote_average or 0.0),
                }
        return result
    finally:
        db.close()


def iter_user_ratings(path: Path) -> Iterable[tuple[int, list[dict[str, Any]]]]:
    current_user_id: int | None = None
    current_rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            user_id = parse_int(row.get("userId"))
            movie_id = parse_int(row.get("movieId"))
            rating = parse_float(row.get("rating"))
            if user_id is None or movie_id is None or rating is None:
                continue
            if current_user_id is None:
                current_user_id = user_id
            if user_id != current_user_id:
                yield current_user_id, current_rows
                current_user_id = user_id
                current_rows = []
            current_rows.append(
                {
                    "userId": user_id,
                    "movieId": movie_id,
                    "rating": rating,
                    "timestamp": parse_int(row.get("timestamp")),
                }
            )
    if current_user_id is not None:
        yield current_user_id, current_rows


def chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def write_jsonl(file: TextIO, payload: dict[str, Any]) -> None:
    file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def open_output(path: Path, *, gzip_output: bool) -> TextIO:
    if gzip_output:
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8", buffering=1024 * 1024)


def parse_user_ids(value: str | None) -> set[int] | None:
    if not value:
        return None
    return {int(item.strip()) for item in value.split(",") if item.strip()}


if __name__ == "__main__":
    main()
