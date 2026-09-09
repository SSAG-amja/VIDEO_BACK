from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from lightfm import LightFM
from scipy.sparse import coo_matrix, csr_matrix, hstack, identity
from sqlalchemy import select

from app.db.session import SessionLocal
from app.jobs.recsys.v3.datasets.dataset_schemas import PositiveInteraction
from app.jobs.recsys.v3.features.feature_representation import (
    transform_user_feature_export,
)
from app.jobs.recsys.v3.features.user_feature_builder import build_user_feature_export
from app.models.user import User
from app.services.recsys.v3.config import (
    LIGHTFM_HYBRID_EPOCHS,
    LIGHTFM_HYBRID_ITEM_ALPHA,
    LIGHTFM_HYBRID_LEARNING_RATE,
    LIGHTFM_HYBRID_MAX_SAMPLED,
    LIGHTFM_HYBRID_NO_COMPONENTS,
    LIGHTFM_HYBRID_USER_ALPHA,
    TRAINING_ITEM_FREQUENCY_MULTIPLIER_MIN,
    TRAINING_RECENCY_MIN_MULTIPLIER,
    TRAINING_USER_ACTIVITY_MULTIPLIER_MIN,
)
from app.services.recsys.v3.domain.behavior import SnapshotAction
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
)
from app.services.recsys.v3.retrieval.score_calibration import (
    collaborative_adjusted_user_representations,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.metrics import evaluation_cutoffs, ndcg_at_k, rating_relevance
from tests.v3_long_term_eval.prepare import normalized_training_times


ROOT = Path("tests/v3_long_term_eval")
DEFAULT_OUTPUT_DIR = ROOT / "outputs/ablation"
SIGNAL_POLICIES = {"baseline", "v3_no_decay", "v3_decay"}


@dataclass(frozen=True, slots=True)
class TrainingSignals:
    interactions: coo_matrix
    sample_weights: coo_matrix
    positives: tuple[PositiveInteraction, ...]


@dataclass(frozen=True, slots=True)
class TrainingVariant:
    name: str
    description: str
    loss: str
    signal_policy: str
    item_frequency_weighting: bool
    user_activity_weighting: bool
    feature_mode: str
    user_identity_weight: float = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run controlled LightFM/V3 long-term NDCG ablations"
    )
    parser.add_argument("--training-user-count", type=int, default=500)
    parser.add_argument("--evaluation-user-count", type=int, default=100)
    parser.add_argument("--epochs", type=int, default=LIGHTFM_HYBRID_EPOCHS)
    parser.add_argument("--num-threads", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    training_plan_path = (
        ROOT
        / "generated/cohorts"
        / str(args.training_user_count)
        / "evaluation_plan.jsonl"
    )
    evaluation_plan_path = (
        ROOT
        / "generated/cohorts"
        / str(args.evaluation_user_count)
        / "evaluation_plan.jsonl"
    )
    training_plans = load_plan(training_plan_path)
    evaluation_plans = load_plan(evaluation_plan_path)
    artifact_path = current_artifact_path(args.training_user_count)
    artifact = load_runtime_hybrid_artifact(artifact_path)
    attach_user_ids(training_plans, artifact.user_ids)
    attach_user_ids(evaluation_plans, artifact.user_ids)

    if len(training_plans) != args.training_user_count:
        raise ValueError("training plan count does not match the requested cohort")
    if len(evaluation_plans) != args.evaluation_user_count:
        raise ValueError("evaluation plan count does not match the requested anchor")

    movie_ids = np.asarray(artifact.movie_ids, dtype=np.int64)
    movie_id_map = {int(movie_id): index for index, movie_id in enumerate(movie_ids)}
    user_ids = tuple(int(user_id) for user_id in artifact.user_ids)
    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    signal_cache = {
        policy: build_training_signals(
            training_plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy=policy,
        )
        for policy in sorted(SIGNAL_POLICIES)
    }

    current_signals = corrected_signals(
        signal_cache["v3_decay"],
        item_frequency_weighting=True,
        user_activity_weighting=True,
    )
    assert_current_training_inputs(artifact, current_signals)

    print("[1/4] current artifact score adjustments", flush=True)
    results: list[dict] = []
    for name, description, confidence, centering in (
        (
            "current_raw",
            "Current V3 model before collaborative confidence and centering",
            False,
            0.0,
        ),
        (
            "current_confidence_only",
            "Current V3 model with collaborative identity confidence only",
            True,
            0.0,
        ),
        (
            "current_centering_only",
            "Current V3 model with 0.9 score centering only",
            False,
            artifact.known_user_score_centering_weight,
        ),
        (
            "current_serving",
            "Current V3 model with collaborative confidence and 0.9 centering",
            True,
            artifact.known_user_score_centering_weight,
        ),
    ):
        rows = evaluate_model(
            artifact.model,
            evaluation_plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            user_features=artifact.user_features,
            item_features=artifact.item_features,
            confidence_enabled=confidence,
            centering_weight=centering,
            population_confidence=artifact.collaborative_population_confidence,
            component_means=artifact.user_representation_component_means,
        )
        results.append(result_summary(name, description, rows, training_seconds=0.0))

    current_item_features = artifact.item_features
    current_user_features = artifact.user_features
    item_manifest = SimpleNamespace(
        ontology_build_id=artifact.ontology_build_id,
        ontology_source_hash=str(artifact.manifest["ontology"]["source_hash"]),
        export_hash=str(artifact.manifest["feature_exports"]["item_export_hash"]),
    )
    item_export = SimpleNamespace(
        movie_ids=tuple(int(value) for value in movie_ids),
        movie_id_map=movie_id_map,
        feature_tokens=artifact.item_feature_tokens,
        feature_token_map={
            token: index for index, token in enumerate(artifact.item_feature_tokens)
        },
        item_features=current_item_features,
        manifest=item_manifest,
    )
    current_model = artifact.model
    del artifact
    del current_model
    gc.collect()

    print("[2/4] prepare fixed ontology representations", flush=True)
    user_feature_cache: dict[tuple[str, float], csr_matrix] = {}

    def user_features_for(policy: str, identity_weight: float) -> csr_matrix:
        key = (policy, identity_weight)
        if key not in user_feature_cache:
            raw_export = build_user_feature_export(
                user_ids=user_ids,
                explicit_genre_rows=(),
                favorite_rows=(),
                item_export=item_export,
                positive_interactions=signal_cache[policy].positives,
            )
            user_feature_cache[key] = transform_user_feature_export(
                raw_export,
                policy="supported_identity_field_budgeted",
                identity_weight=identity_weight,
                semantic_weight=0.5,
            ).user_features
        return user_feature_cache[key]

    supported_item_identity = current_item_features[:, : len(movie_ids)].tocsr()
    training_variants = experiment_variants()
    total_variants = len(training_variants)
    for ordinal, variant in enumerate(training_variants, start=1):
        print(
            f"[3/4] train {ordinal}/{total_variants} {variant.name}",
            flush=True,
        )
        signals = corrected_signals(
            signal_cache[variant.signal_policy],
            item_frequency_weighting=variant.item_frequency_weighting,
            user_activity_weighting=variant.user_activity_weighting,
        )
        signals = signals_for_loss(signals, loss=variant.loss)
        user_features, item_features = feature_matrices(
            variant.feature_mode,
            supported_item_identity=supported_item_identity,
            current_item_features=current_item_features,
            current_user_features=user_features_for(
                variant.signal_policy,
                variant.user_identity_weight,
            ),
            user_count=len(user_ids),
            user_identity_weight=variant.user_identity_weight,
        )
        started = time.perf_counter()
        model = LightFM(
            no_components=LIGHTFM_HYBRID_NO_COMPONENTS,
            loss=variant.loss,
            learning_rate=LIGHTFM_HYBRID_LEARNING_RATE,
            user_alpha=LIGHTFM_HYBRID_USER_ALPHA,
            item_alpha=LIGHTFM_HYBRID_ITEM_ALPHA,
            max_sampled=LIGHTFM_HYBRID_MAX_SAMPLED,
            random_state=42,
        )
        model.fit(
            signals.interactions,
            sample_weight=signals.sample_weights,
            user_features=user_features,
            item_features=item_features,
            epochs=args.epochs,
            num_threads=args.num_threads,
            verbose=False,
        )
        training_seconds = time.perf_counter() - started
        rows = evaluate_model(
            model,
            evaluation_plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            user_features=user_features,
            item_features=item_features,
        )
        summary = result_summary(
            variant.name,
            variant.description,
            rows,
            training_seconds=training_seconds,
        )
        summary["training"] = {
            "loss": variant.loss,
            "signal_policy": variant.signal_policy,
            "item_frequency_weighting": variant.item_frequency_weighting,
            "user_activity_weighting": variant.user_activity_weighting,
            "feature_mode": variant.feature_mode,
            "interaction_count": int(signals.interactions.nnz),
            "sample_weight_mean": float(signals.sample_weights.data.mean()),
        }
        results.append(summary)
        del model
        gc.collect()

    report = build_report(
        results,
        artifact_path=artifact_path,
        training_user_count=len(training_plans),
        evaluation_user_count=len(evaluation_plans),
        movie_count=len(movie_ids),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "ablation_results.json"
    markdown_path = args.output_dir / "ablation_results.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report_markdown(report), encoding="utf-8")
    print(f"[4/4] complete: {markdown_path}", flush=True)


def experiment_variants() -> tuple[TrainingVariant, ...]:
    return (
        TrainingVariant(
            "warp_without_recency",
            "Current WARP hybrid with recency removed; other training corrections retained",
            "warp",
            "v3_no_decay",
            True,
            True,
            "current_hybrid",
        ),
        TrainingVariant(
            "warp_without_item_frequency",
            "Current WARP hybrid without popular-item sample attenuation",
            "warp",
            "v3_decay",
            False,
            True,
            "current_hybrid",
        ),
        TrainingVariant(
            "warp_without_user_activity",
            "Current WARP hybrid without high-activity-user sample attenuation",
            "warp",
            "v3_decay",
            True,
            False,
            "current_hybrid",
        ),
        TrainingVariant(
            "warp_without_training_corrections",
            "Current WARP hybrid without item-frequency or user-activity attenuation",
            "warp",
            "v3_decay",
            False,
            False,
            "current_hybrid",
        ),
        TrainingVariant(
            "logistic_pass_current",
            "Replace WARP positive-only learning with logistic and explicit PASS negatives",
            "logistic",
            "v3_decay",
            True,
            True,
            "current_hybrid",
        ),
        TrainingVariant(
            "logistic_pass_without_recency",
            "Logistic/PASS hybrid with V3 action weights and no recency attenuation",
            "logistic",
            "v3_no_decay",
            True,
            True,
            "current_hybrid",
        ),
        TrainingVariant(
            "logistic_pass_without_corrections",
            "Logistic/PASS hybrid with V3 action weights and no attenuation",
            "logistic",
            "v3_no_decay",
            False,
            False,
            "current_hybrid",
        ),
        TrainingVariant(
            "pure_logistic_full_catalog",
            "Pure identity LightFM baseline on the same full V3 movie mapping",
            "logistic",
            "baseline",
            False,
            False,
            "pure_identity",
            user_identity_weight=1.0,
        ),
        TrainingVariant(
            "logistic_supported_identity_only",
            "Baseline labels with current supported item/user identity blocks only",
            "logistic",
            "baseline",
            False,
            False,
            "supported_identity_only",
        ),
        TrainingVariant(
            "logistic_item_ontology",
            "Add current item ontology features to supported identity blocks",
            "logistic",
            "baseline",
            False,
            False,
            "item_ontology",
        ),
        TrainingVariant(
            "logistic_full_ontology",
            "Add current long-term user ontology profile to item ontology features",
            "logistic",
            "baseline",
            False,
            False,
            "current_hybrid",
        ),
    )


def build_training_signals(
    plans: list[dict],
    *,
    user_id_map: dict[int, int],
    movie_id_map: dict[int, int],
    signal_policy: str,
    positive_half_life_days: float | None = None,
    passed_half_life_days: float | None = None,
    recency_min_multiplier: float = TRAINING_RECENCY_MIN_MULTIPLIER,
) -> TrainingSignals:
    if signal_policy not in SIGNAL_POLICIES:
        raise ValueError(f"unknown signal policy: {signal_policy}")
    rows: list[int] = []
    columns: list[int] = []
    labels: list[float] = []
    weights: list[float] = []
    positives: list[PositiveInteraction] = []
    for plan in plans:
        user_id = int(plan["user_id"])
        user_index = user_id_map[user_id]
        as_of = datetime.fromisoformat(str(plan["evaluation_as_of"]))
        training_times = normalized_training_times(
            tuple(SimpleNamespace(**rating) for rating in plan["train"]),
            as_of=as_of,
        )
        for rating, occurred_at in zip(plan["train"], training_times, strict=True):
            value = float(rating["rating"])
            signal = signal_for_rating(
                value,
                occurred_at=occurred_at,
                as_of=as_of,
                policy=signal_policy,
                positive_half_life_days=positive_half_life_days,
                passed_half_life_days=passed_half_life_days,
                recency_min_multiplier=recency_min_multiplier,
            )
            if signal is None:
                continue
            label, weight, action = signal
            movie_id = int(rating["movie_id"])
            movie_index = movie_id_map.get(movie_id)
            if movie_index is None:
                raise ValueError(f"training movie is absent from V3 mapping: {movie_id}")
            rows.append(user_index)
            columns.append(movie_index)
            labels.append(label)
            weights.append(weight)
            if label > 0:
                positives.append(
                    PositiveInteraction(
                        user_id=user_id,
                        movie_id=movie_id,
                        actions=(action,),
                        representative_action=action,
                        sample_weight=weight,
                        latest_at=occurred_at,
                    )
                )
    shape = (len(user_id_map), len(movie_id_map))
    row_values = np.asarray(rows, dtype=np.int32)
    column_values = np.asarray(columns, dtype=np.int32)
    label_values = np.asarray(labels, dtype=np.float32)
    weight_values = np.asarray(weights, dtype=np.float32)
    order = np.lexsort((column_values, row_values))
    coordinates = (row_values[order], column_values[order])
    return TrainingSignals(
        interactions=coo_matrix(
            (label_values[order], coordinates),
            shape=shape,
            dtype=np.float32,
        ),
        sample_weights=coo_matrix(
            (weight_values[order], coordinates),
            shape=shape,
            dtype=np.float32,
        ),
        positives=tuple(sorted(positives, key=lambda item: (item.user_id, item.movie_id))),
    )


def signal_for_rating(
    rating: float,
    *,
    occurred_at: datetime,
    as_of: datetime,
    policy: str,
    positive_half_life_days: float | None = None,
    passed_half_life_days: float | None = None,
    recency_min_multiplier: float = TRAINING_RECENCY_MIN_MULTIPLIER,
) -> tuple[float, float, SnapshotAction] | None:
    if 2.0 <= rating <= 3.0:
        return None
    if rating <= 1.5:
        action = SnapshotAction.PASSED
        base_weight = 1.0
        label = -1.0
        half_life = passed_half_life_days or 90.0
    elif 3.5 <= rating <= 4.5:
        action = SnapshotAction.PINNED
        base_weight = 1.0 if policy == "baseline" else 2.0
        label = 1.0
        half_life = positive_half_life_days or 60.0
    elif rating == 5.0:
        action = SnapshotAction.SAVED
        base_weight = 1.5 if policy == "baseline" else 2.0
        label = 1.0
        half_life = positive_half_life_days or 60.0
    else:
        return None
    if policy == "v3_decay":
        age_days = max(0.0, (as_of - occurred_at).total_seconds() / 86_400.0)
        base_weight *= max(
            recency_min_multiplier,
            math.pow(0.5, age_days / half_life),
        )
    return label, base_weight, action


def corrected_signals(
    signals: TrainingSignals,
    *,
    item_frequency_weighting: bool,
    user_activity_weighting: bool,
) -> TrainingSignals:
    interactions = signals.interactions.tocoo(copy=False)
    weights = signals.sample_weights.tocoo(copy=True)
    positive = interactions.data > 0
    adjusted = weights.data.astype(np.float32, copy=True)
    if item_frequency_weighting:
        supports = np.bincount(
            interactions.col[positive],
            minlength=interactions.shape[1],
        )
        positive_supports = supports[supports > 0]
        reference = float(np.median(positive_supports))
        item_multipliers = np.ones(interactions.shape[1], dtype=np.float32)
        supported = supports > 0
        item_multipliers[supported] = np.clip(
            np.sqrt(reference / supports[supported].astype(np.float64)),
            TRAINING_ITEM_FREQUENCY_MULTIPLIER_MIN,
            1.0,
        )
        adjusted *= item_multipliers[interactions.col]
    if user_activity_weighting:
        positive_totals = np.bincount(
            interactions.row[positive],
            weights=adjusted[positive].astype(np.float64),
            minlength=interactions.shape[0],
        )
        active_totals = positive_totals[positive_totals > 0]
        reference = float(np.median(active_totals))
        user_multipliers = np.ones(interactions.shape[0], dtype=np.float32)
        above = positive_totals > reference
        user_multipliers[above] = np.clip(
            reference / positive_totals[above],
            TRAINING_USER_ACTIVITY_MULTIPLIER_MIN,
            1.0,
        )
        adjusted *= user_multipliers[interactions.row]
    return TrainingSignals(
        interactions=interactions,
        sample_weights=coo_matrix(
            (adjusted, (weights.row, weights.col)),
            shape=weights.shape,
            dtype=np.float32,
        ),
        positives=signals.positives,
    )


def signals_for_loss(signals: TrainingSignals, *, loss: str) -> TrainingSignals:
    if loss == "logistic":
        return signals
    if loss != "warp":
        raise ValueError(f"unsupported experiment loss: {loss}")
    interactions = signals.interactions.tocoo(copy=False)
    weights = signals.sample_weights.tocoo(copy=False)
    positive = interactions.data > 0
    return TrainingSignals(
        interactions=coo_matrix(
            (
                interactions.data[positive].astype(np.float32, copy=False),
                (interactions.row[positive], interactions.col[positive]),
            ),
            shape=interactions.shape,
            dtype=np.float32,
        ),
        sample_weights=coo_matrix(
            (
                weights.data[positive].astype(np.float32, copy=False),
                (weights.row[positive], weights.col[positive]),
            ),
            shape=weights.shape,
            dtype=np.float32,
        ),
        positives=signals.positives,
    )


def feature_matrices(
    mode: str,
    *,
    supported_item_identity: csr_matrix,
    current_item_features: csr_matrix,
    current_user_features: csr_matrix,
    user_count: int,
    user_identity_weight: float,
) -> tuple[csr_matrix | None, csr_matrix | None]:
    if mode == "pure_identity":
        return None, None
    user_identity = identity(user_count, format="csr", dtype=np.float32)
    user_identity *= user_identity_weight
    if mode == "supported_identity_only":
        return user_identity, supported_item_identity
    if mode == "item_ontology":
        return user_identity, current_item_features
    if mode == "current_hybrid":
        return current_user_features, current_item_features
    raise ValueError(f"unknown feature mode: {mode}")


def evaluate_model(
    model,
    plans: list[dict],
    *,
    user_id_map: dict[int, int],
    movie_id_map: dict[int, int],
    user_features: csr_matrix | None,
    item_features: csr_matrix | None,
    confidence_enabled: bool = False,
    centering_weight: float = 0.0,
    population_confidence: float = 1.0,
    component_means=None,
) -> list[dict]:
    rows = []
    for plan in plans:
        user_index = user_id_map[int(plan["user_id"])]
        if user_features is None:
            user_bias = float(model.user_biases[user_index])
            user_embedding = np.asarray(model.user_embeddings[user_index], dtype=np.float32)
        elif confidence_enabled or centering_weight > 0:
            confidence = (
                assess_user_collaborative_confidence(
                    positive_pair_count=int(plan["summary"]["train_positive_count"]),
                    population_confidence=population_confidence,
                ).effective_confidence
                if confidence_enabled
                else 1.0
            )
            biases, embeddings = collaborative_adjusted_user_representations(
                model,
                user_features[user_index],
                identity_feature_count=len(user_id_map),
                collaborative_confidences=np.asarray([confidence], dtype=np.float32),
                centering_weight=centering_weight,
                component_means=component_means if centering_weight > 0 else None,
            )
            user_bias = float(biases[0])
            user_embedding = np.asarray(embeddings[0], dtype=np.float32)
        else:
            biases, embeddings = model.get_user_representations(user_features[user_index])
            user_bias = float(biases[0])
            user_embedding = np.asarray(embeddings[0], dtype=np.float32)

        candidate_movie_ids = np.asarray(
            [int(item["movie_id"]) for item in plan["holdout"]],
            dtype=np.int64,
        )
        item_indices = np.asarray(
            [movie_id_map[int(movie_id)] for movie_id in candidate_movie_ids],
            dtype=np.int64,
        )
        if item_features is None:
            item_biases = model.item_biases[item_indices]
            item_embeddings = model.item_embeddings[item_indices]
        else:
            item_biases, item_embeddings = model.get_item_representations(
                item_features[item_indices]
            )
        scores = np.asarray(item_embeddings, dtype=np.float32) @ user_embedding
        scores += user_bias
        scores += (1.0 - centering_weight) * np.asarray(item_biases, dtype=np.float32)
        order = np.lexsort((candidate_movie_ids, -scores))
        ranking = [int(movie_id) for movie_id in candidate_movie_ids[order]]
        relevance = {
            int(item["movie_id"]): rating_relevance(float(item["rating"]))
            for item in plan["holdout"]
            if float(item["rating"]) >= 3.5
        }
        candidate_count = len(candidate_movie_ids)
        ndcg = {
            str(cutoff): ndcg_at_k(ranking, relevance, cutoff)
            for cutoff in evaluation_cutoffs(candidate_count)
        }
        rows.append(
            {
                "source_user_id": int(plan["source_user_id"]),
                "candidate_count": candidate_count,
                "ndcg": ndcg,
            }
        )
    return rows


def result_summary(
    name: str,
    description: str,
    rows: list[dict],
    *,
    training_seconds: float,
) -> dict:
    return {
        "name": name,
        "description": description,
        "training_seconds": training_seconds,
        "ndcg_at_10": mean_cutoff(rows, 10),
        "ndcg_at_20": mean_cutoff(rows, 20),
        "ndcg_at_30": mean_cutoff(rows, 30),
        "mean_full_holdout_ndcg": statistics.mean(
            float(row["ndcg"][str(row["candidate_count"])]) for row in rows
        ),
        "per_user_ndcg_at_20": {
            str(row["source_user_id"]): float(row["ndcg"]["20"]) for row in rows
        },
    }


def mean_cutoff(rows: list[dict], cutoff: int) -> float:
    values = [float(row["ndcg"][str(cutoff)]) for row in rows if str(cutoff) in row["ndcg"]]
    return statistics.mean(values)


def build_report(
    results: list[dict],
    *,
    artifact_path: Path,
    training_user_count: int,
    evaluation_user_count: int,
    movie_count: int,
) -> dict:
    by_name = {row["name"]: row for row in results}
    comparisons = []
    for name, reference, changed, question in comparison_plan():
        before = by_name[reference]
        after = by_name[changed]
        delta20 = float(after["ndcg_at_20"]) - float(before["ndcg_at_20"])
        delta30 = float(after["ndcg_at_30"]) - float(before["ndcg_at_30"])
        comparisons.append(
            {
                "name": name,
                "question": question,
                "reference": reference,
                "changed": changed,
                "delta_ndcg_at_20": delta20,
                "delta_ndcg_at_30": delta30,
                "verdict": effect_verdict(delta20, delta30),
            }
        )
    return {
        "scope": "V3 long-term preference-ranking controlled ablation",
        "controls": {
            "training_user_count": training_user_count,
            "evaluation_user_count": evaluation_user_count,
            "movie_count": movie_count,
            "candidate_set": "Each anchor user's temporal 30% holdout only",
            "random_seed": 42,
            "epochs": LIGHTFM_HYBRID_EPOCHS,
            "active_bundle_changed": False,
            "source_artifact": str(artifact_path),
        },
        "plan": [
            "Measure confidence and centering on the same current artifact.",
            "Remove recency, item-frequency, and user-activity corrections one at a time.",
            "Compare WARP positive-only learning with logistic explicit PASS learning.",
            "Add item and user ontology features under the same logistic labels.",
        ],
        "results": results,
        "comparisons": comparisons,
        "winner_at_20": max(results, key=lambda row: row["ndcg_at_20"])["name"],
        "winner_at_30": max(results, key=lambda row: row["ndcg_at_30"])["name"],
    }


def comparison_plan() -> tuple[tuple[str, str, str, str], ...]:
    return (
        (
            "collaborative_confidence",
            "current_raw",
            "current_confidence_only",
            "Does collaborative identity attenuation improve preference ranking?",
        ),
        (
            "score_centering",
            "current_raw",
            "current_centering_only",
            "Does 0.9 score centering improve preference ranking?",
        ),
        (
            "combined_serving_adjustment",
            "current_raw",
            "current_serving",
            "Do the two current serving adjustments help together?",
        ),
        (
            "recency_under_warp",
            "current_raw",
            "warp_without_recency",
            "Does removing long-term interaction recency improve WARP?",
        ),
        (
            "item_frequency_under_warp",
            "current_raw",
            "warp_without_item_frequency",
            "Does removing item-frequency attenuation improve WARP?",
        ),
        (
            "user_activity_under_warp",
            "current_raw",
            "warp_without_user_activity",
            "Does removing user-activity attenuation improve WARP?",
        ),
        (
            "loss_and_explicit_pass",
            "current_raw",
            "logistic_pass_current",
            "Does logistic learning with explicit PASS improve the current training inputs?",
        ),
        (
            "recency_under_logistic",
            "logistic_pass_current",
            "logistic_pass_without_recency",
            "Does removing recency improve logistic/PASS learning?",
        ),
        (
            "training_corrections_under_logistic",
            "logistic_pass_without_recency",
            "logistic_pass_without_corrections",
            "Does removing both training attenuations improve logistic/PASS learning?",
        ),
        (
            "item_ontology_features",
            "logistic_supported_identity_only",
            "logistic_item_ontology",
            "Do current item ontology features improve ranking under fixed labels?",
        ),
        (
            "user_ontology_features",
            "logistic_item_ontology",
            "logistic_full_ontology",
            "Does the current long-term user ontology profile add value?",
        ),
    )


def effect_verdict(delta20: float, delta30: float) -> str:
    average = (delta20 + delta30) / 2.0
    if average >= 0.005:
        return "favorable"
    if average <= -0.005:
        return "unfavorable"
    return "neutral"


def report_markdown(report: dict) -> str:
    lines = [
        "# V3 장기 취향 LightFM 통제 실험",
        "",
        "## 통제 조건",
        "",
        f"- 학습 사용자: `{report['controls']['training_user_count']}`명",
        f"- 고정 평가 사용자: `{report['controls']['evaluation_user_count']}`명",
        f"- 영화 매핑: `{report['controls']['movie_count']}`편",
        "- 후보: 사용자별 시간순 뒤쪽 30% 영화만 사용",
        "- 운영 모델과 serving bundle은 변경하지 않음",
        "",
        "## 실험 계획",
        "",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(report["plan"], start=1))
    lines.extend(
        [
            "",
            "## 측정 결과",
            "",
            "| 변형 | NDCG@10 | NDCG@20 | NDCG@30 | 전체 holdout | 학습 초 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["results"]:
        lines.append(
            f"| `{row['name']}` | {row['ndcg_at_10']:.6f} | "
            f"{row['ndcg_at_20']:.6f} | {row['ndcg_at_30']:.6f} | "
            f"{row['mean_full_holdout_ndcg']:.6f} | {row['training_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 조건별 판정",
            "",
            "| 확인 항목 | 기준 -> 변경 | NDCG@20 차이 | NDCG@30 차이 | 판정 |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in report["comparisons"]:
        lines.append(
            f"| `{row['name']}` | `{row['reference']}` -> `{row['changed']}` | "
            f"{row['delta_ndcg_at_20']:+.6f} | {row['delta_ndcg_at_30']:+.6f} | "
            f"{row['verdict']} |"
        )
    lines.extend(
        [
            "",
            "## 최고 결과",
            "",
            f"- NDCG@20: `{report['winner_at_20']}`",
            f"- NDCG@30: `{report['winner_at_30']}`",
            "",
            "`favorable`은 NDCG@20과 @30 차이 평균이 `+0.005` 이상인 경우다.",
            "이 평가는 취향 순위 판별만 측정하며 전체 카탈로그 후보 검색 품질은 측정하지 않는다.",
            "",
        ]
    )
    return "\n".join(lines)


def current_artifact_path(training_user_count: int) -> Path:
    comparison = json.loads((ROOT / "outputs/scale_comparison.json").read_text())
    model_build_id = next(
        str(row["model_build_id"])
        for row in comparison["rows"]
        if int(row["training_user_count"]) == training_user_count
    )
    path = ROOT / "generated/models" / str(training_user_count) / model_build_id
    if not path.is_dir():
        raise FileNotFoundError(f"current evaluation artifact is missing: {path}")
    return path


def attach_user_ids(plans: list[dict], artifact_user_ids: np.ndarray) -> None:
    emails = [str(plan["email"]) for plan in plans]
    with SessionLocal() as db:
        ids_by_email = dict(
            db.execute(select(User.email, User.id).where(User.email.in_(emails)))
            .tuples()
            .all()
        )
    artifact_ids = {int(value) for value in artifact_user_ids}
    for plan in plans:
        user_id = ids_by_email.get(str(plan["email"]))
        if user_id is None or int(user_id) not in artifact_ids:
            raise ValueError(f"evaluation user is missing from artifact: {plan['email']}")
        plan["user_id"] = int(user_id)


def assert_current_training_inputs(artifact, signals: TrainingSignals) -> None:
    diagnostics = json.loads((artifact.path / "diagnostics.json").read_text())
    expected_count = int(diagnostics["interaction_nnz"])
    expected_mean = float(diagnostics["sample_weight"]["mean"])
    positive = signals.interactions.data > 0
    actual_count = int(np.count_nonzero(positive))
    actual_mean = float(signals.sample_weights.data[positive].mean())
    if actual_count != expected_count or not math.isclose(
        actual_mean,
        expected_mean,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise ValueError(
            "reconstructed current training inputs differ from the source artifact "
            f"count={actual_count}/{expected_count} mean={actual_mean}/{expected_mean}"
        )


if __name__ == "__main__":
    main()
