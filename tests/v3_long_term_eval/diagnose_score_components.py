from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix

from app.services.recsys.v3.retrieval.score_calibration import (
    without_user_identity_features,
)
from app.services.recsys.v3.serving.model_store import (
    RuntimeHybridArtifact,
    load_runtime_hybrid_artifact,
)
from tests.v3_long_term_eval.analyze_single_user import (
    enrich_metadata,
    load_db_metadata,
    load_jsonl_record,
    load_movielens_movies,
    load_source_by_tmdb,
)
from tests.v3_long_term_eval.evaluate import exact_top_k_indices
from tests.v3_long_term_eval.metrics import evaluate_ranking, rating_relevance


ROOT = Path("tests/v3_long_term_eval")
DEFAULT_MOVIES = (609319, 13, 275)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decompose V3 LightFM scores for one evaluation user"
    )
    parser.add_argument("source_user_id", type=int)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--movie-ids", type=int, nargs="+", default=DEFAULT_MOVIES)
    parser.add_argument("--limit", type=int, default=165)
    parser.add_argument("--item-block-size", type=int, default=8192)
    parser.add_argument(
        "--plan", type=Path, default=ROOT / "generated/evaluation_plan.jsonl"
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=ROOT / "outputs/long_term_evaluation_results.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "single_user_score_components.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit <= 0 or args.item_block_size <= 0:
        raise SystemExit("limit and item block size must be positive")
    plan = load_jsonl_record(args.plan, args.source_user_id)
    result = load_jsonl_record(args.results, args.source_user_id)
    artifact = load_runtime_hybrid_artifact(args.artifact)
    user_id = int(result["user_id"])
    user_index = artifact.user_index(user_id)
    if user_index is None:
        raise SystemExit(f"user_id={user_id} is missing from the artifact")
    confidence = float(
        result["final"]["retrieval_diagnostics"][
            "collaborative_effective_confidence"
        ]
    )
    components = user_components(artifact, user_index)
    score_rows = [
        movie_score_components(
            artifact,
            movie_id=int(movie_id),
            confidence=confidence,
            components=components,
        )
        for movie_id in args.movie_ids
    ]

    exclusions = {int(item["movie_id"]) for item in plan["train"]}
    rankings = score_variants(
        artifact,
        components=components,
        current_confidence=confidence,
        exclusions=exclusions,
        limit=args.limit,
        item_block_size=args.item_block_size,
    )
    relevance = {
        int(item["movie_id"]): rating_relevance(float(item["rating"]))
        for item in plan["holdout"]
        if rating_relevance(float(item["rating"])) > 0.0
    }
    metadata_ids = set(args.movie_ids)
    for ranking in rankings.values():
        metadata_ids.update(movie_id for movie_id, _score in ranking[:20])
    metadata = enrich_metadata(
        load_db_metadata(metadata_ids),
        load_source_by_tmdb(ROOT / "inputs/links.csv"),
        load_movielens_movies(ROOT / "inputs/movies.csv"),
    )
    for row in score_rows:
        row.update(display_metadata(metadata[row["movie_id"]]))

    variant_reports = {}
    for name, ranking in rankings.items():
        movie_ids = [movie_id for movie_id, _score in ranking]
        metrics = evaluate_ranking(
            movie_ids,
            relevance,
            cutoffs=(20, 50, 100, args.limit),
        )
        variant_reports[name] = {
            "ndcg": {str(key): value for key, value in metrics.ndcg.items()},
            "top_20": [
                {
                    "rank": rank,
                    "score": score,
                    **item_feature_summary(artifact, movie_id),
                    **display_metadata(metadata[movie_id]),
                }
                for rank, (movie_id, score) in enumerate(ranking[:20], start=1)
            ],
        }

    payload = {
        "source_user_id": args.source_user_id,
        "user_id": user_id,
        "current_confidence": confidence,
        "centering_weight": artifact.known_user_score_centering_weight,
        "user_feature_count": int(artifact.user_features[user_index].nnz),
        "holdout_relevant_count": len(relevance),
        "movie_score_components": score_rows,
        "ranking_variants": variant_reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", "output": str(args.output)}, sort_keys=True))


def user_components(artifact: RuntimeHybridArtifact, user_index: int) -> dict[str, np.ndarray | float]:
    user_features = artifact.user_features[user_index : user_index + 1]
    full_bias, full_embedding = artifact.model.get_user_representations(user_features)
    semantic_features = without_user_identity_features(
        user_features,
        identity_feature_count=len(artifact.user_ids),
    )
    semantic_bias, semantic_embedding = artifact.model.get_user_representations(
        semantic_features
    )
    full_bias_value = float(np.asarray(full_bias).reshape(-1)[0])
    semantic_bias_value = float(np.asarray(semantic_bias).reshape(-1)[0])
    full_embedding_value = np.asarray(full_embedding, dtype=np.float32).reshape(1, -1)[0]
    semantic_embedding_value = np.asarray(
        semantic_embedding, dtype=np.float32
    ).reshape(1, -1)[0]
    return {
        "semantic_bias": semantic_bias_value,
        "semantic_embedding": semantic_embedding_value,
        "identity_bias": full_bias_value - semantic_bias_value,
        "identity_embedding": full_embedding_value - semantic_embedding_value,
    }


def centered_user_components(
    artifact: RuntimeHybridArtifact,
    components: dict[str, np.ndarray | float],
) -> dict[str, np.ndarray | float]:
    weight = artifact.known_user_score_centering_weight
    means = artifact.user_representation_component_means
    return {
        "semantic_bias": float(components["semantic_bias"])
        - weight * means.semantic_bias,
        "semantic_embedding": np.asarray(components["semantic_embedding"])
        - weight * means.semantic_embedding,
        "identity_bias": float(components["identity_bias"])
        - weight * means.identity_bias,
        "identity_embedding": np.asarray(components["identity_embedding"])
        - weight * means.identity_embedding,
    }


def movie_score_components(
    artifact: RuntimeHybridArtifact,
    *,
    movie_id: int,
    confidence: float,
    components: dict[str, np.ndarray | float],
) -> dict:
    movie_index = artifact.movie_index(movie_id)
    if movie_index is None:
        raise SystemExit(f"movie_id={movie_id} is missing from the artifact")
    item_features = artifact.item_features[movie_index : movie_index + 1]
    item_biases, item_embeddings = artifact.model.get_item_representations(item_features)
    item_bias = float(np.asarray(item_biases).reshape(-1)[0])
    item_embedding = np.asarray(item_embeddings, dtype=np.float32).reshape(1, -1)[0]
    centered = centered_user_components(artifact, components)
    semantic = component_score(centered, "semantic", item_bias, item_embedding)
    identity_unscaled = component_score(centered, "identity", item_bias, item_embedding)
    item_bias_centered = (
        1.0 - artifact.known_user_score_centering_weight
    ) * item_bias
    current_score = semantic + confidence * identity_unscaled + item_bias_centered
    confidence_one_score = semantic + identity_unscaled + item_bias_centered
    uncentered_semantic = component_score(
        components, "semantic", item_bias, item_embedding
    )
    uncentered_identity = component_score(
        components, "identity", item_bias, item_embedding
    )
    uncentered_score = uncentered_semantic + confidence * uncentered_identity + item_bias
    return {
        "movie_id": movie_id,
        "current": {
            "semantic": semantic,
            "identity_before_confidence": identity_unscaled,
            "identity_after_confidence": confidence * identity_unscaled,
            "item_bias": item_bias_centered,
            "total": current_score,
        },
        "confidence_one_total": confidence_one_score,
        "uncentered_current_confidence_total": uncentered_score,
        "centering_effect": current_score - uncentered_score,
        **item_feature_summary(artifact, movie_id),
    }


def component_score(
    components: dict[str, np.ndarray | float],
    prefix: str,
    _item_bias: float,
    item_embedding: np.ndarray,
) -> float:
    return float(components[f"{prefix}_bias"]) + float(
        np.asarray(components[f"{prefix}_embedding"]) @ item_embedding
    )


def score_variants(
    artifact: RuntimeHybridArtifact,
    *,
    components: dict[str, np.ndarray | float],
    current_confidence: float,
    exclusions: set[int],
    limit: int,
    item_block_size: int,
) -> dict[str, list[tuple[int, float]]]:
    centered = centered_user_components(artifact, components)
    semantic_embedding = np.asarray(centered["semantic_embedding"], dtype=np.float32)
    identity_embedding = np.asarray(centered["identity_embedding"], dtype=np.float32)
    semantic_bias = float(centered["semantic_bias"])
    identity_bias = float(centered["identity_bias"])
    user_representations = {
        "current_confidence": (
            semantic_embedding + current_confidence * identity_embedding,
            semantic_bias + current_confidence * identity_bias,
        ),
        "confidence_one": (
            semantic_embedding + identity_embedding,
            semantic_bias + identity_bias,
        ),
    }
    variants = {
        "current_confidence": ("current_confidence", None),
        "confidence_one": ("confidence_one", None),
        "field_coverage_gamma_050": ("current_confidence", 0.50),
        "field_coverage_gamma_075": ("current_confidence", 0.75),
        "field_coverage_gamma_100": ("current_confidence", 1.00),
    }
    top_ids = {name: np.empty(0, dtype=np.int64) for name in variants}
    top_scores = {name: np.empty(0, dtype=np.float32) for name in variants}
    feature_group_codes = item_feature_group_codes(artifact)
    for start in range(0, len(artifact.movie_ids), item_block_size):
        end = min(start + item_block_size, len(artifact.movie_ids))
        item_features = artifact.item_features[start:end]
        item_representations = {}
        for gamma in (None, 0.50, 0.75, 1.00):
            transformed = (
                item_features
                if gamma is None
                else field_coverage_features(
                    item_features,
                    feature_group_codes=feature_group_codes,
                    gamma=gamma,
                )
            )
            item_biases, item_embeddings = artifact.model.get_item_representations(
                transformed
            )
            item_representations[gamma] = (
                np.asarray(item_biases, dtype=np.float32),
                np.asarray(item_embeddings, dtype=np.float32),
            )
        movie_ids = np.asarray(artifact.movie_ids[start:end], dtype=np.int64)
        excluded_mask = np.fromiter(
            (int(movie_id) in exclusions for movie_id in movie_ids),
            dtype=np.bool_,
            count=len(movie_ids),
        )
        for name, (user_variant, gamma) in variants.items():
            user_embedding, user_bias = user_representations[user_variant]
            item_biases, item_embeddings = item_representations[gamma]
            scores = item_embeddings @ user_embedding
            scores += user_bias
            scores += (1.0 - artifact.known_user_score_centering_weight) * item_biases
            scores[excluded_mask] = -np.inf
            selected = exact_top_k_indices(scores, movie_ids, limit)
            merged_ids = np.concatenate((top_ids[name], movie_ids[selected]))
            merged_scores = np.concatenate((top_scores[name], scores[selected]))
            keep = exact_top_k_indices(merged_scores, merged_ids, limit)
            top_ids[name] = merged_ids[keep]
            top_scores[name] = merged_scores[keep].astype(np.float32, copy=False)
    return {
        name: [
            (int(movie_id), float(score))
            for movie_id, score in zip(top_ids[name], top_scores[name], strict=True)
        ]
        for name in variants
    }


def item_feature_group_codes(artifact: RuntimeHybridArtifact) -> np.ndarray:
    movie_count = len(artifact.movie_ids)
    group_by_family = {
        "genre": 0,
        "keyword": 1,
        "actor": 2,
        "director": 3,
        "theme": 4,
        "mood": 4,
    }
    codes = np.full(len(artifact.item_feature_tokens), -1, dtype=np.int8)
    for index, token in enumerate(
        artifact.item_feature_tokens[movie_count:],
        start=movie_count,
    ):
        family = token.split(":", 1)[0]
        try:
            codes[index] = group_by_family[family]
        except KeyError as exc:
            raise ValueError(f"unsupported item feature family: {family}") from exc
    return codes


def field_coverage_features(
    matrix: csr_matrix,
    *,
    feature_group_codes: np.ndarray,
    gamma: float,
) -> csr_matrix:
    if gamma <= 0.0:
        raise ValueError("coverage gamma must be positive")
    source = matrix.tocoo(copy=True)
    group_codes = feature_group_codes[source.col]
    semantic = group_codes >= 0
    if not np.any(semantic):
        return matrix
    group_sums = np.zeros((matrix.shape[0], 5), dtype=np.float32)
    np.add.at(
        group_sums,
        (source.row[semantic], group_codes[semantic]),
        source.data[semantic],
    )
    active_counts = np.count_nonzero(group_sums > 0.0, axis=1)
    semantic_rows = source.row[semantic]
    semantic_groups = group_codes[semantic]
    denominators = group_sums[semantic_rows, semantic_groups]
    row_active_counts = active_counts[semantic_rows].astype(np.float32)
    coverage = np.power(row_active_counts / 5.0, gamma).astype(np.float32)
    source.data[semantic] = (
        source.data[semantic]
        / denominators
        / row_active_counts
        * coverage
    )
    transformed = source.tocsr()
    transformed.sum_duplicates()
    transformed.sort_indices()
    return transformed


def item_feature_summary(artifact: RuntimeHybridArtifact, movie_id: int) -> dict:
    movie_index = artifact.movie_index(movie_id)
    if movie_index is None:
        return {"movie_id": movie_id}
    row = artifact.item_features[movie_index]
    identity_present = bool(movie_index in row.indices)
    semantic_indices = row.indices[row.indices >= len(artifact.movie_ids)]
    families = sorted(
        {
            artifact.item_feature_tokens[int(index)].split(":", 1)[0]
            for index in semantic_indices
        }
    )
    semantic_features = None
    if len(semantic_indices) <= 10:
        semantic_features = [
            artifact.item_feature_tokens[int(index)] for index in semantic_indices
        ]
    return {
        "movie_id": movie_id,
        "identity_present": identity_present,
        "semantic_feature_count": int(len(semantic_indices)),
        "semantic_families": families,
        "semantic_features": semantic_features,
    }


def display_metadata(item: dict) -> dict:
    return {
        "tmdb_id": item["tmdb_id"],
        "title": item["movielens_title"] or item["title"],
        "genres": item["genres"],
        "vote_average": item["vote_average"],
        "vote_count": item["vote_count"],
    }


if __name__ == "__main__":
    main()
