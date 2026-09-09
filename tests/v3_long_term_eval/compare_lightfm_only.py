from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.db.session import SessionLocal
from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.training.artifact_publisher import read_json
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_identity_model
from app.models.user import User
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
    population_confidence_from_diagnostics,
)
from app.services.recsys.v3.retrieval.score_calibration import (
    collaborative_adjusted_user_representations,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from tests.v3_long_term_eval.evaluate import exact_top_k_indices, load_plan
from tests.v3_long_term_eval.evaluation_dataset import restrict_to_evaluation_users
from tests.v3_long_term_eval.metrics import (
    evaluate_ranking,
    evaluation_cutoffs,
    rating_relevance,
)


ROOT = Path("tests/v3_long_term_eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare identity-only and hybrid LightFM on one holdout user."
    )
    parser.add_argument("hybrid_artifact", type=Path)
    parser.add_argument("--source-user-id", type=int, required=True)
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "generated/evaluation_plan.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/lightfm_only_comparison.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifact = load_runtime_hybrid_artifact(args.hybrid_artifact)
    plan = next(
        (
            row
            for row in load_plan(args.plan)
            if int(row["source_user_id"]) == args.source_user_id
        ),
        None,
    )
    if plan is None:
        raise SystemExit(f"source user is missing from plan: {args.source_user_id}")

    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.email == str(plan["email"])))
        if user_id is None:
            raise SystemExit(f"evaluation user is missing from DB: {plan['email']}")
        dataset = restrict_to_evaluation_users(
            build_lightfm_dataset(
                db,
                data_cutoff_at=datetime.fromisoformat(
                    artifact.manifest["data_cutoff_at"]
                ),
            ),
            user_ids=tuple(int(value) for value in artifact.user_ids),
        )
    if dataset.diagnostics.dataset_hash != artifact.manifest["dataset_hash"]:
        raise SystemExit("identity/hybrid comparison dataset does not match the artifact")
    if tuple(int(value) for value in artifact.movie_ids) != dataset.movie_ids:
        raise SystemExit("identity/hybrid comparison movie mappings do not match")

    hybrid_config = LightFMTrainingConfig(
        **read_json(args.hybrid_artifact / "config.json")
    )
    identity_config = replace(hybrid_config, stage="identity_only")
    identity = train_identity_model(dataset, identity_config)
    user_index = dataset.user_id_map[int(user_id)]
    candidate_count = int(plan["candidate_count"])
    excluded_indices = indices_for_movie_ids(
        dataset.movie_ids,
        {int(item["movie_id"]) for item in plan["train"]},
    )
    supported_indices = np.unique(dataset.interactions.tocoo(copy=False).col).astype(
        np.int64,
        copy=False,
    )
    relevance = {
        int(item["movie_id"]): rating_relevance(float(item["rating"]))
        for item in plan["holdout"]
        if float(item["rating"]) >= 3.5
        and int(item["movie_id"]) in dataset.movie_id_map
    }

    identity_raw_bias = float(identity.model.user_biases[user_index])
    identity_raw_embedding = np.asarray(
        identity.model.user_embeddings[user_index],
        dtype=np.float32,
    )
    centering_weight = identity_config.known_user_score_centering_weight
    population_confidence = population_confidence_from_diagnostics(
        identity.diagnostics,
        user_count=len(dataset.user_ids),
    )
    identity_confidence = assess_user_collaborative_confidence(
        positive_pair_count=int(plan["summary"]["train_positive_count"]),
        population_confidence=population_confidence,
    ).effective_confidence
    identity_effective_bias = identity_confidence * (
        identity_raw_bias
        - centering_weight * float(np.mean(identity.model.user_biases, dtype=np.float64))
    )
    identity_effective_embedding = identity_confidence * (
        identity_raw_embedding
        - centering_weight
        * np.mean(identity.model.user_embeddings, axis=0, dtype=np.float64)
    )

    hybrid_user_row = artifact.user_features[artifact.user_index(int(user_id))]
    hybrid_raw_biases, hybrid_raw_embeddings = artifact.model.get_user_representations(
        hybrid_user_row
    )
    hybrid_effective_biases, hybrid_effective_embeddings = (
        collaborative_adjusted_user_representations(
            artifact.model,
            hybrid_user_row,
            identity_feature_count=len(artifact.user_ids),
            collaborative_confidences=np.asarray(
                [
                    assess_user_collaborative_confidence(
                        positive_pair_count=int(
                            plan["summary"]["train_positive_count"]
                        ),
                        population_confidence=artifact.collaborative_population_confidence,
                    ).effective_confidence
                ],
                dtype=np.float32,
            ),
            centering_weight=artifact.known_user_score_centering_weight,
            component_means=artifact.user_representation_component_means,
        )
    )

    rankings = {
        "identity_only_raw_supported": rank_identity(
            identity.model,
            movie_ids=np.asarray(dataset.movie_ids, dtype=np.int64),
            user_bias=identity_raw_bias,
            user_embedding=identity_raw_embedding,
            item_bias_scale=1.0,
            allowed_indices=supported_indices,
            excluded_indices=excluded_indices,
            top_k=candidate_count,
        ),
        "identity_only_effective_supported": rank_identity(
            identity.model,
            movie_ids=np.asarray(dataset.movie_ids, dtype=np.int64),
            user_bias=float(identity_effective_bias),
            user_embedding=np.asarray(identity_effective_embedding, dtype=np.float32),
            item_bias_scale=1.0 - centering_weight,
            allowed_indices=supported_indices,
            excluded_indices=excluded_indices,
            top_k=candidate_count,
        ),
        "hybrid_raw_supported": rank_hybrid(
            artifact,
            user_bias=float(hybrid_raw_biases[0]),
            user_embedding=np.asarray(hybrid_raw_embeddings[0], dtype=np.float32),
            item_bias_scale=1.0,
            allowed_indices=supported_indices,
            excluded_indices=excluded_indices,
            top_k=candidate_count,
        ),
        "hybrid_effective_supported": rank_hybrid(
            artifact,
            user_bias=float(hybrid_effective_biases[0]),
            user_embedding=np.asarray(hybrid_effective_embeddings[0], dtype=np.float32),
            item_bias_scale=1.0 - artifact.known_user_score_centering_weight,
            allowed_indices=supported_indices,
            excluded_indices=excluded_indices,
            top_k=candidate_count,
        ),
        "hybrid_effective_full_catalog": rank_hybrid(
            artifact,
            user_bias=float(hybrid_effective_biases[0]),
            user_embedding=np.asarray(hybrid_effective_embeddings[0], dtype=np.float32),
            item_bias_scale=1.0 - artifact.known_user_score_centering_weight,
            allowed_indices=None,
            excluded_indices=excluded_indices,
            top_k=candidate_count,
        ),
    }
    cutoffs = evaluation_cutoffs(candidate_count)
    results = {
        label: ranking_result(ranking, relevance, cutoffs=cutoffs)
        for label, ranking in rankings.items()
    }
    report = {
        "source_user_id": args.source_user_id,
        "db_user_id": int(user_id),
        "dataset_hash": dataset.diagnostics.dataset_hash,
        "candidate_count": candidate_count,
        "catalog_movie_count": len(dataset.movie_ids),
        "training_supported_movie_count": int(supported_indices.size),
        "holdout_positive_count": len(relevance),
        "holdout_identity_supported_count": sum(
            dataset.movie_id_map[movie_id] in supported_indices for movie_id in relevance
        ),
        "training_config": identity_config.as_dict(),
        "identity_population_confidence": population_confidence,
        "identity_user_confidence": identity_confidence,
        "hybrid_population_confidence": artifact.collaborative_population_confidence,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


def rank_identity(
    model,
    *,
    movie_ids: np.ndarray,
    user_bias: float,
    user_embedding: np.ndarray,
    item_bias_scale: float,
    allowed_indices: np.ndarray | None,
    excluded_indices: np.ndarray,
    top_k: int,
) -> list[int]:
    return rank_blocks(
        movie_ids=movie_ids,
        user_bias=user_bias,
        user_embedding=user_embedding,
        item_bias_scale=item_bias_scale,
        item_representation=lambda start, end: (
            model.item_biases[start:end],
            model.item_embeddings[start:end],
        ),
        allowed_indices=allowed_indices,
        excluded_indices=excluded_indices,
        top_k=top_k,
    )


def rank_hybrid(
    artifact,
    *,
    user_bias: float,
    user_embedding: np.ndarray,
    item_bias_scale: float,
    allowed_indices: np.ndarray | None,
    excluded_indices: np.ndarray,
    top_k: int,
) -> list[int]:
    return rank_blocks(
        movie_ids=np.asarray(artifact.movie_ids, dtype=np.int64),
        user_bias=user_bias,
        user_embedding=user_embedding,
        item_bias_scale=item_bias_scale,
        item_representation=lambda start, end: artifact.model.get_item_representations(
            artifact.item_features[start:end]
        ),
        allowed_indices=allowed_indices,
        excluded_indices=excluded_indices,
        top_k=top_k,
    )


def rank_blocks(
    *,
    movie_ids: np.ndarray,
    user_bias: float,
    user_embedding: np.ndarray,
    item_bias_scale: float,
    item_representation,
    allowed_indices: np.ndarray | None,
    excluded_indices: np.ndarray,
    top_k: int,
    block_size: int = 8_192,
) -> list[int]:
    allowed = None
    if allowed_indices is not None:
        allowed = np.zeros(len(movie_ids), dtype=bool)
        allowed[allowed_indices] = True
    excluded = np.zeros(len(movie_ids), dtype=bool)
    excluded[excluded_indices] = True
    top_ids = np.empty(0, dtype=np.int64)
    top_scores = np.empty(0, dtype=np.float32)
    for start in range(0, len(movie_ids), block_size):
        end = min(start + block_size, len(movie_ids))
        item_biases, item_embeddings = item_representation(start, end)
        scores = np.asarray(item_embeddings, dtype=np.float32) @ user_embedding
        scores += user_bias
        scores += item_bias_scale * np.asarray(item_biases, dtype=np.float32)
        invalid = excluded[start:end]
        if allowed is not None:
            invalid = invalid | ~allowed[start:end]
        scores[invalid] = -np.inf
        selected = exact_top_k_indices(scores, movie_ids[start:end], top_k)
        merged_ids = np.concatenate((top_ids, movie_ids[start:end][selected]))
        merged_scores = np.concatenate((top_scores, scores[selected]))
        keep = exact_top_k_indices(merged_scores, merged_ids, top_k)
        top_ids = merged_ids[keep]
        top_scores = merged_scores[keep].astype(np.float32, copy=False)
    return [int(value) for value in top_ids]


def indices_for_movie_ids(
    movie_ids: tuple[int, ...],
    selected_movie_ids: set[int],
) -> np.ndarray:
    mapping = {movie_id: index for index, movie_id in enumerate(movie_ids)}
    return np.asarray(
        sorted(
            mapping[movie_id]
            for movie_id in selected_movie_ids
            if movie_id in mapping
        ),
        dtype=np.int64,
    )


def ranking_result(
    ranking: list[int],
    relevance: dict[int, float],
    *,
    cutoffs: tuple[int, ...],
) -> dict:
    metrics = evaluate_ranking(ranking, relevance, cutoffs=cutoffs)
    return {
        "returned_count": len(ranking),
        "hits": {
            str(k): sum(movie_id in relevance for movie_id in ranking[:k])
            for k in cutoffs
        },
        "ndcg": {str(k): value for k, value in metrics.ndcg.items()},
        "ranked_movie_ids": ranking,
    }


if __name__ == "__main__":
    main()
