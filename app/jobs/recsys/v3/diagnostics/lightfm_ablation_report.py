from __future__ import annotations

import argparse
import gc
import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from statistics import mean

import numpy as np
from sqlalchemy import select

from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.db.session import SessionLocal
from app.jobs.recsys.v3.candidates.candidate_materializer import (
    materialize_candidate_batch,
)
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig
from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.diagnostics.quality_snapshot import (
    REPRESENTATIVE_PROFILE_TYPES,
    attach_metadata,
    catalog_quality,
    genre_alignment,
    load_movie_metadata,
    load_representative_users,
)
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact
from app.models.mapping import user_genres


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "z_v3_docs" / "diagnostics"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare inactive LightFM artifacts on the same representative users."
    )
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--display-limit", type=int, default=20)
    parser.add_argument(
        "--zero-item-bias",
        action="store_true",
        help="Diagnostic only: rank with all learned item biases set to zero in memory.",
    )
    parser.add_argument(
        "--center-known-user-scores",
        action="store_true",
        help="Diagnostic only: subtract the mean known-user score per item.",
    )
    parser.add_argument("--center-known-user-weight", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.display_limit <= args.top_k <= 100:
        raise SystemExit("require 1 <= display-limit <= top-k <= 100")
    if not 0.0 <= args.center_known_user_weight <= 1.0:
        raise SystemExit("center-known-user-weight must be in [0, 1]")
    center_weight = (
        1.0 if args.center_known_user_scores else args.center_known_user_weight
    )

    with SessionLocal() as db:
        users = load_representative_users(db)
        user_ids = tuple(item["user_id"] for item in users)
        _, exclusions = load_eligible_users_and_exclusions(db, user_ids)
        genres_by_user = load_user_genres(db, user_ids)

    model_reports: list[dict] = []
    candidates_by_model: dict[str, dict[int, tuple[int, ...]]] = {}
    dataset_hashes: set[str] = set()
    for artifact_path in args.artifacts:
        artifact = load_hybrid_artifact(artifact_path)
        artifact_center_weight = artifact.config.known_user_score_centering_weight
        if center_weight > 0 and artifact_center_weight > 0:
            raise ValueError("diagnostic centering cannot wrap an already centered artifact")
        effective_center_weight = center_weight or artifact_center_weight
        if args.zero_item_bias:
            artifact.model.item_biases.fill(0.0)
        if center_weight > 0:
            artifact = replace(
                artifact,
                model=CenteredKnownUserModel(
                    artifact.model,
                    artifact.user_features,
                    weight=center_weight,
                ),
            )
        exports = artifact.manifest["feature_exports"]
        policy = str(exports.get("item_representation_policy", "full_identity_raw"))
        label = (
            f"{policy}:"
            f"u{exports.get('user_identity_block_weight', 1.0)}+s"
            f"{exports.get('user_semantic_block_weight', 1.0)}:"
            f"i{exports.get('item_identity_block_weight', 1.0)}+s"
            f"{exports.get('item_semantic_block_weight', 1.0)}:"
            f"freq-{artifact.config.item_frequency_weighting}:"
            f"item-bias-{'zero' if args.zero_item_bias else 'learned'}:"
            f"center-{effective_center_weight}:"
            f"c{artifact.config.no_components}-e{artifact.config.epochs}-"
            f"lr{artifact.config.learning_rate}"
        )
        if label in candidates_by_model:
            raise ValueError(f"duplicate representation policy in ablation: {label}")
        user_index = {int(user_id): index for index, user_id in enumerate(artifact.user_ids)}
        missing = [user_id for user_id in user_ids if user_id not in user_index]
        if missing:
            raise ValueError(f"artifact is missing representative users: {missing}")
        batch = materialize_candidate_batch(
            artifact,
            [user_index[user_id] for user_id in user_ids],
            exclusions_by_user_id=exclusions,
            config=CandidateMaterializationConfig(
                top_k=args.top_k,
                user_block_size=len(user_ids),
            ),
        )
        if batch.failures:
            raise RuntimeError(f"candidate materialization failed: {batch.failures}")
        grouped = group_candidates(batch)
        with SessionLocal() as db:
            metadata = load_movie_metadata(
                db,
                {
                    item["movie_id"]
                    for candidates in grouped.values()
                    for item in candidates
                },
            )
        model_report = summarize_model(
            artifact=artifact,
            label=label,
            users=users,
            grouped=grouped,
            scores=group_scores(batch),
            genres_by_user=genres_by_user,
            metadata=metadata,
            display_limit=args.display_limit,
            elapsed_seconds=batch.elapsed_seconds,
            center_weight=effective_center_weight,
        )
        model_reports.append(model_report)
        candidates_by_model[label] = {
            user_id: tuple(item["movie_id"] for item in candidates)
            for user_id, candidates in grouped.items()
        }
        dataset_hashes.add(str(artifact.manifest["dataset_hash"]))
        del artifact, batch
        gc.collect()

    cutoff = datetime.fromisoformat(model_reports[0]["data_cutoff_at"])
    with SessionLocal() as db:
        dataset = build_lightfm_dataset(db, data_cutoff_at=cutoff)
        training_data = summarize_training_data(db, dataset, user_ids)
        training_data["matches_artifacts"] = dataset.diagnostics.dataset_hash in dataset_hashes

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "artifact_count": len(model_reports),
            "representative_user_count": len(users),
            "top_k": args.top_k,
            "display_limit": args.display_limit,
            "same_dataset_hash": len(dataset_hashes) == 1,
            "dataset_hashes": sorted(dataset_hashes),
            "metric_note": (
                "Genre alignment is a fixture sanity signal, not relevance ground truth. "
                "No NDCG or Recall is computed."
            ),
        },
        "models": model_reports,
        "training_data": training_data,
        "cross_model_overlap": compare_models(candidates_by_model),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_lightfm_ablation_{timestamp}.json"
    markdown_path = args.output_dir / f"v3_lightfm_ablation_{timestamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}))


def load_user_genres(db, user_ids: tuple[int, ...]) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for user_id, genre_id in db.execute(
        select(user_genres.c.user_id, user_genres.c.genre_id).where(
            user_genres.c.user_id.in_(user_ids)
        )
    ):
        result[int(user_id)].add(int(genre_id))
    return result


class CenteredKnownUserModel:
    def __init__(self, model, user_features, *, weight: float) -> None:
        self._model = model
        self._weight = weight
        biases, embeddings = model.get_user_representations(user_features)
        self._mean_user_bias = float(np.mean(biases))
        self._mean_user_embedding = np.mean(embeddings, axis=0, dtype=np.float64).astype(
            np.float32
        )

    def __getattr__(self, name):
        return getattr(self._model, name)

    def get_user_representations(self, features=None):
        biases, embeddings = self._model.get_user_representations(features)
        return (
            np.asarray(biases, dtype=np.float32) - self._weight * self._mean_user_bias,
            np.asarray(embeddings, dtype=np.float32)
            - self._weight * self._mean_user_embedding,
        )

    def get_item_representations(self, features=None):
        biases, embeddings = self._model.get_item_representations(features)
        return (1.0 - self._weight) * np.asarray(biases, dtype=np.float32), embeddings


def group_candidates(batch) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for user_id, movie_id, score, rank in zip(
        batch.candidate_user_ids,
        batch.movie_ids,
        batch.model_scores,
        batch.source_ranks,
        strict=True,
    ):
        grouped[int(user_id)].append(
            {"movie_id": int(movie_id), "score": float(score), "rank": int(rank)}
        )
    return grouped


def group_scores(batch) -> np.ndarray:
    return np.asarray(batch.model_scores, dtype=np.float64)


def summarize_model(
    *,
    artifact,
    label: str,
    users: list[dict],
    grouped: dict[int, list[dict]],
    scores: np.ndarray,
    genres_by_user: dict[int, set[int]],
    metadata: dict[int, dict],
    display_limit: int,
    elapsed_seconds: float,
    center_weight: float,
) -> dict:
    per_user: list[dict] = []
    candidate_sets: list[set[int]] = []
    movie_frequency: Counter[int] = Counter()
    for user in users:
        candidates = attach_metadata(grouped[user["user_id"]], metadata)
        candidate_sets.append({item["movie_id"] for item in candidates})
        movie_frequency.update(item["movie_id"] for item in candidates)
        top_display = candidates[:display_limit]
        per_user.append(
            {
                **user,
                "target_genre_ids": sorted(genres_by_user[user["user_id"]]),
                "top_display_alignment": genre_alignment(
                    top_display, genres_by_user[user["user_id"]]
                ),
                "top_k_alignment": genre_alignment(
                    candidates, genres_by_user[user["user_id"]]
                ),
                "top_movies": [
                    {
                        "rank": item["rank"],
                        "movie_id": item["movie_id"],
                        "tmdb_id": item.get("tmdb_id"),
                        "title": item.get("title"),
                        "score": round(item["score"], 6),
                        "genre_ids": item.get("genre_ids", []),
                    }
                    for item in top_display
                ],
            }
        )

    profile_summary = {}
    for profile_type in REPRESENTATIVE_PROFILE_TYPES:
        rows = [item for item in per_user if item["profile_type"] == profile_type]
        profile_summary[profile_type] = {
            "top_display_any_overlap_rate": round(
                mean(item["top_display_alignment"]["any_overlap_rate"] for item in rows),
                6,
            ),
            "top_display_mean_genre_share": round(
                mean(item["top_display_alignment"]["mean_genre_share"] for item in rows),
                6,
            ),
            "top_k_any_overlap_rate": round(
                mean(item["top_k_alignment"]["any_overlap_rate"] for item in rows),
                6,
            ),
        }
    all_candidates = [item for candidates in grouped.values() for item in candidates]
    all_with_metadata = attach_metadata(all_candidates, metadata)
    display_sets = [
        {item["movie_id"] for item in grouped[user["user_id"]][:display_limit]}
        for user in users
    ]
    display_frequency = Counter(
        item["movie_id"]
        for user in users
        for item in grouped[user["user_id"]][:display_limit]
    )
    return {
        "label": label,
        "artifact_path": str(artifact.path),
        "model_build_id": artifact.manifest["model_build_id"],
        "dataset_hash": artifact.manifest["dataset_hash"],
        "data_cutoff_at": artifact.manifest["data_cutoff_at"],
        "training_config": artifact.config.as_dict(),
        "model_health": json.loads((artifact.path / "diagnostics.json").read_text())["model_health"],
        "candidate_elapsed_seconds": round(elapsed_seconds, 6),
        "raw_score": distribution(scores),
        "score_components": (
            {"mode": "known_user_mean_centered", "weight": center_weight}
            if center_weight > 0
            else score_component_summary(artifact, grouped)
        ),
        "profile_summary": profile_summary,
        "concentration": {
            "unique_movie_count": len(movie_frequency),
            "pairwise_jaccard_mean": round(pairwise_jaccard(candidate_sets), 6),
            "max_user_frequency": max(movie_frequency.values(), default=0),
            "top_display_unique_movie_count": len(display_frequency),
            "top_display_pairwise_jaccard_mean": round(
                pairwise_jaccard(display_sets), 6
            ),
            "top_display_max_user_frequency": max(display_frequency.values(), default=0),
            "top_repeated_movies": [
                {
                    "movie_id": movie_id,
                    "user_count": count,
                    "tmdb_id": metadata.get(movie_id, {}).get("tmdb_id"),
                    "title": metadata.get(movie_id, {}).get("title"),
                }
                for movie_id, count in movie_frequency.most_common(10)
            ],
        },
        "catalog_quality": catalog_quality(all_with_metadata),
        "users": per_user,
    }


def distribution(values: np.ndarray) -> dict[str, float | int]:
    return {
        "count": int(values.size),
        "min": float(values.min()),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


def summarize_training_data(db, dataset, representative_user_ids: tuple[int, ...]) -> dict:
    interactions = dataset.interactions.tocsr(copy=False)
    representative_sets: list[set[int]] = []
    for user_id in representative_user_ids:
        row = dataset.user_id_map[user_id]
        start = int(interactions.indptr[row])
        end = int(interactions.indptr[row + 1])
        representative_sets.append(
            {dataset.movie_ids[int(index)] for index in interactions.indices[start:end]}
        )
    coo = interactions.tocoo(copy=False)
    support = Counter(int(index) for index in coo.col)
    top_indices = [index for index, _count in support.most_common(10)]
    metadata = load_movie_metadata(
        db,
        {dataset.movie_ids[index] for index in top_indices},
    )
    return {
        "dataset_hash": dataset.diagnostics.dataset_hash,
        "user_count": len(dataset.user_ids),
        "positive_pair_count": int(interactions.nnz),
        "unique_positive_movie_count": len(support),
        "representative_pairwise_positive_jaccard": round(
            pairwise_jaccard(representative_sets), 6
        ),
        "max_movie_user_support": max(support.values(), default=0),
        "top_supported_movies": [
            {
                "movie_id": dataset.movie_ids[index],
                "user_count": support[index],
                "tmdb_id": metadata.get(dataset.movie_ids[index], {}).get("tmdb_id"),
                "title": metadata.get(dataset.movie_ids[index], {}).get("title"),
            }
            for index in top_indices
        ],
    }


def score_component_summary(artifact, grouped: dict[int, list[dict]]) -> dict:
    user_index = {int(user_id): index for index, user_id in enumerate(artifact.user_ids)}
    movie_ids = np.asarray(artifact.movie_ids, dtype=np.int64)
    values: dict[str, list[float]] = defaultdict(list)
    reconstruction_errors: list[float] = []
    for user_id, candidates in grouped.items():
        user_row = user_index[user_id]
        user_features = artifact.user_features[user_row]
        full_user_bias, full_user_embedding = artifact.model.get_user_representations(
            user_features
        )
        user_identity_weight = float(user_features[0, user_row])
        user_identity_embedding = (
            artifact.model.user_embeddings[user_row].astype(np.float64)
            * user_identity_weight
        )
        user_semantic_embedding = (
            np.asarray(full_user_embedding[0], dtype=np.float64)
            - user_identity_embedding
        )
        user_identity_bias = float(artifact.model.user_biases[user_row]) * user_identity_weight
        user_semantic_bias = float(full_user_bias[0]) - user_identity_bias

        candidate_movie_ids = np.asarray(
            [item["movie_id"] for item in candidates], dtype=np.int64
        )
        item_indices = np.searchsorted(movie_ids, candidate_movie_ids)
        item_features = artifact.item_features[item_indices]
        full_item_biases, full_item_embeddings = artifact.model.get_item_representations(
            item_features
        )
        item_identity_weights = np.asarray(
            artifact.item_features[item_indices, item_indices]
        ).reshape(-1)
        item_identity_embeddings = (
            artifact.model.item_embeddings[item_indices].astype(np.float64)
            * item_identity_weights[:, None]
        )
        item_semantic_embeddings = (
            np.asarray(full_item_embeddings, dtype=np.float64) - item_identity_embeddings
        )
        item_identity_biases = (
            artifact.model.item_biases[item_indices].astype(np.float64)
            * item_identity_weights
        )
        item_semantic_biases = (
            np.asarray(full_item_biases, dtype=np.float64) - item_identity_biases
        )
        components = {
            "user_identity_x_item_identity": item_identity_embeddings @ user_identity_embedding,
            "user_identity_x_item_semantic": item_semantic_embeddings @ user_identity_embedding,
            "user_semantic_x_item_identity": item_identity_embeddings @ user_semantic_embedding,
            "user_semantic_x_item_semantic": item_semantic_embeddings @ user_semantic_embedding,
            "user_identity_bias": np.full(len(candidates), user_identity_bias),
            "user_semantic_bias": np.full(len(candidates), user_semantic_bias),
            "item_identity_bias": item_identity_biases,
            "item_semantic_bias": item_semantic_biases,
        }
        reconstructed = np.zeros(len(candidates), dtype=np.float64)
        for name, component in components.items():
            reconstructed += component
            values[name].extend(float(value) for value in component)
        expected = np.asarray([item["score"] for item in candidates], dtype=np.float64)
        reconstruction_errors.extend(np.abs(reconstructed - expected))
    return {
        name: {
            "mean": round(float(np.mean(component)), 6),
            "mean_abs": round(float(np.mean(np.abs(component))), 6),
            "p95_abs": round(float(np.percentile(np.abs(component), 95)), 6),
        }
        for name, component in sorted(values.items())
    } | {"max_reconstruction_error": max(reconstruction_errors, default=0.0)}


def pairwise_jaccard(candidate_sets: list[set[int]]) -> float:
    values = [
        len(left & right) / len(left | right)
        for left, right in combinations(candidate_sets, 2)
        if left or right
    ]
    return mean(values) if values else 0.0


def compare_models(
    candidates_by_model: dict[str, dict[int, tuple[int, ...]]]
) -> list[dict]:
    result = []
    for left_label, right_label in combinations(sorted(candidates_by_model), 2):
        left = candidates_by_model[left_label]
        right = candidates_by_model[right_label]
        overlaps = []
        for user_id in sorted(set(left) & set(right)):
            left_set = set(left[user_id])
            right_set = set(right[user_id])
            overlaps.append(len(left_set & right_set) / len(left_set | right_set))
        result.append(
            {
                "left": left_label,
                "right": right_label,
                "mean_user_jaccard": round(mean(overlaps), 6) if overlaps else 0.0,
            }
        )
    return result


def render_markdown(report: dict) -> str:
    lines = [
        "# V3 LightFM Ablation",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- same_dataset_hash: `{report['scope']['same_dataset_hash']}`",
        f"- representative_users: `{report['scope']['representative_user_count']}`",
        f"- top_k: `{report['scope']['top_k']}`",
        "",
        "## Model Summary",
        "",
        "| representation | score min/median/max | unique movies | pairwise Jaccard | candidate seconds |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        score = model["raw_score"]
        concentration = model["concentration"]
        lines.append(
            f"| {model['label']} | {score['min']:.3f} / {score['median']:.3f} / {score['max']:.3f} "
            f"| {concentration['unique_movie_count']} | {concentration['pairwise_jaccard_mean']:.4f} "
            f"| {model['candidate_elapsed_seconds']:.3f} |"
        )
    lines.extend(["", "## Profile Alignment", ""])
    for model in report["models"]:
        lines.extend(
            [
                f"### {model['label']}",
                "",
                "| profile | top20 overlap | top20 genre share | top100 overlap |",
                "|---|---:|---:|---:|",
            ]
        )
        for profile, values in model["profile_summary"].items():
            lines.append(
                f"| {profile} | {values['top_display_any_overlap_rate']:.4f} "
                f"| {values['top_display_mean_genre_share']:.4f} "
                f"| {values['top_k_any_overlap_rate']:.4f} |"
            )
        lines.append("")
    lines.extend(["## Cross-Model Overlap", ""])
    for item in report["cross_model_overlap"]:
        lines.append(
            f"- `{item['left']}` vs `{item['right']}`: `{item['mean_user_jaccard']:.4f}`"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
