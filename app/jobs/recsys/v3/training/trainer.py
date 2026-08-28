from __future__ import annotations

import hashlib
import json
import platform
import time
from itertools import combinations
from importlib import metadata

import numpy as np

from app.jobs.recsys.v3.datasets.dataset_schemas import LightFMDatasetSnapshot
from app.jobs.recsys.v3.features.feature_representation import sparse_row_sum_diagnostics
from app.jobs.recsys.v3.features.feature_schemas import ItemFeatureExport, UserFeatureExport
from app.jobs.recsys.v3.training.model_schemas import (
    HybridTrainingResult,
    IdentityTrainingResult,
    LightFMTrainingConfig,
    PredictionVerification,
)


MODEL_HEALTH_THRESHOLDS = {
    "embedding_max_abs": 100.0,
    "embedding_row_norm_max": 500.0,
    "bias_max_abs": 100.0,
    "prediction_max_abs": 1000.0,
    "candidate_pairwise_jaccard_max": 0.98,
}


class ModelHealthError(RuntimeError):
    def __init__(self, report: dict[str, object]) -> None:
        self.report = report
        violations = ", ".join(str(item) for item in report["violations"])
        super().__init__(f"LightFM model health gate failed: {violations}")
from app.services.recsys.v3.config import (
    TRAINING_ACTION_PRIORITY,
    TRAINING_ACTION_WEIGHTS,
    TRAINING_MAX_SAMPLE_WEIGHT,
    TRAINING_MISSING_TIMESTAMP_MULTIPLIERS,
    TRAINING_OVERLAP_CONFIDENCE_BONUS,
    TRAINING_RECENCY_HALF_LIFE_DAYS,
    TRAINING_RECENCY_MIN_MULTIPLIER,
)
from app.services.recsys.v3.retrieval.score_calibration import (
    center_known_user_representations,
    mean_known_user_representation,
)


def train_identity_model(
    dataset: LightFMDatasetSnapshot,
    config: LightFMTrainingConfig | None = None,
) -> IdentityTrainingResult:
    from lightfm import LightFM

    training_config = config or LightFMTrainingConfig()
    if training_config.stage != "identity_only":
        raise ValueError("identity trainer requires the identity_only stage")
    interactions, sample_weights = validate_identity_dataset(dataset)
    sample_weights, frequency_weighting = apply_item_frequency_weighting(
        interactions,
        sample_weights,
        mode=training_config.item_frequency_weighting,
    )
    started = time.monotonic()
    model = LightFM(
        no_components=training_config.no_components,
        loss=training_config.loss,
        learning_rate=training_config.learning_rate,
        user_alpha=training_config.user_alpha,
        item_alpha=training_config.item_alpha,
        max_sampled=training_config.max_sampled,
        random_state=training_config.random_seed,
    )
    model.fit(
        interactions,
        sample_weight=sample_weights,
        epochs=training_config.epochs,
        num_threads=training_config.num_threads,
        verbose=False,
    )
    elapsed_seconds = time.monotonic() - started
    verification = build_prediction_verification(
        model,
        user_count=len(dataset.user_ids),
        movie_count=len(dataset.movie_ids),
        num_threads=training_config.num_threads,
    )
    model_health = evaluate_model_health(
        model,
        user_count=len(dataset.user_ids),
        movie_count=len(dataset.movie_ids),
        num_threads=training_config.num_threads,
        score_centering_weight=training_config.known_user_score_centering_weight,
    )
    assert_model_health(model_health)
    versions = package_versions()
    data_policy = training_data_policy_snapshot()
    data_policy_hash = hash_json_payload(data_policy)
    diagnostics = {
        "stage": training_config.stage,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "user_count": len(dataset.user_ids),
        "movie_count": len(dataset.movie_ids),
        "interaction_nnz": int(interactions.nnz),
        "interaction_density": float(
            interactions.nnz / (interactions.shape[0] * interactions.shape[1])
        ),
        "sample_weight": {
            "min": float(sample_weights.data.min()),
            "max": float(sample_weights.data.max()),
            "mean": float(sample_weights.data.mean()),
        },
        "item_frequency_weighting": frequency_weighting,
        "package_versions": versions,
        "model_health": model_health,
        "artifact_reload_exact_match": False,
    }
    return IdentityTrainingResult(
        model=model,
        config=training_config,
        data_cutoff_at=dataset.data_cutoff_at,
        dataset_hash=dataset.diagnostics.dataset_hash,
        user_ids=dataset.user_ids,
        movie_ids=dataset.movie_ids,
        interaction_nnz=int(interactions.nnz),
        training_data_policy=data_policy,
        training_data_policy_hash=data_policy_hash,
        package_versions=versions,
        diagnostics=diagnostics,
        verification=verification,
    )


def train_hybrid_model(
    dataset: LightFMDatasetSnapshot,
    *,
    item_export: ItemFeatureExport,
    user_export: UserFeatureExport,
    config: LightFMTrainingConfig | None = None,
) -> HybridTrainingResult:
    from lightfm import LightFM

    training_config = config or LightFMTrainingConfig(stage="hybrid_ontology")
    if training_config.stage != "hybrid_ontology":
        raise ValueError("hybrid trainer requires the hybrid_ontology stage")
    interactions, sample_weights = validate_identity_dataset(dataset)
    sample_weights, frequency_weighting = apply_item_frequency_weighting(
        interactions,
        sample_weights,
        mode=training_config.item_frequency_weighting,
    )
    user_features, item_features = validate_hybrid_feature_compatibility(
        dataset,
        item_export=item_export,
        user_export=user_export,
    )
    started = time.monotonic()
    model = LightFM(
        no_components=training_config.no_components,
        loss=training_config.loss,
        learning_rate=training_config.learning_rate,
        user_alpha=training_config.user_alpha,
        item_alpha=training_config.item_alpha,
        max_sampled=training_config.max_sampled,
        random_state=training_config.random_seed,
    )
    model.fit(
        interactions,
        sample_weight=sample_weights,
        user_features=user_features,
        item_features=item_features,
        epochs=training_config.epochs,
        num_threads=training_config.num_threads,
        verbose=False,
    )
    elapsed_seconds = time.monotonic() - started
    if model.user_embeddings.shape[0] != user_features.shape[1]:
        raise RuntimeError("hybrid model user feature embedding dimension mismatch")
    if model.item_embeddings.shape[0] != item_features.shape[1]:
        raise RuntimeError("hybrid model item feature embedding dimension mismatch")
    verification = build_prediction_verification(
        model,
        user_count=len(dataset.user_ids),
        movie_count=len(dataset.movie_ids),
        num_threads=training_config.num_threads,
        user_features=user_features,
        item_features=item_features,
    )
    model_health = evaluate_model_health(
        model,
        user_count=len(dataset.user_ids),
        movie_count=len(dataset.movie_ids),
        num_threads=training_config.num_threads,
        user_features=user_features,
        item_features=item_features,
        score_centering_weight=training_config.known_user_score_centering_weight,
    )
    assert_model_health(model_health)
    versions = package_versions()
    data_policy = training_data_policy_snapshot()
    data_policy_hash = hash_json_payload(data_policy)
    diagnostics = {
        "stage": training_config.stage,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "user_count": len(dataset.user_ids),
        "movie_count": len(dataset.movie_ids),
        "interaction_nnz": int(interactions.nnz),
        "user_feature_shape": list(user_features.shape),
        "user_feature_nnz": int(user_features.nnz),
        "item_feature_shape": list(item_features.shape),
        "item_feature_nnz": int(item_features.nnz),
        "ontology_build_id": item_export.manifest.ontology_build_id,
        "item_feature_export_hash": item_export.manifest.export_hash,
        "user_feature_export_hash": user_export.manifest.export_hash,
        "feature_representation": {
            "item": item_export.manifest.representation_policy,
            "user": user_export.manifest.representation_policy,
            "item_identity_block_weight": item_export.manifest.identity_block_weight,
            "item_semantic_block_weight": item_export.manifest.semantic_block_weight,
            "user_identity_block_weight": user_export.manifest.identity_block_weight,
            "user_semantic_block_weight": user_export.manifest.semantic_block_weight,
            "user_row_sum": sparse_row_sum_diagnostics(user_features),
            "item_row_sum": sparse_row_sum_diagnostics(item_features),
        },
        "sample_weight": {
            "min": float(sample_weights.data.min()),
            "max": float(sample_weights.data.max()),
            "mean": float(sample_weights.data.mean()),
        },
        "item_frequency_weighting": frequency_weighting,
        "package_versions": versions,
        "model_health": model_health,
        "artifact_reload_exact_match": False,
    }
    return HybridTrainingResult(
        model=model,
        config=training_config,
        data_cutoff_at=dataset.data_cutoff_at,
        dataset_hash=dataset.diagnostics.dataset_hash,
        user_ids=dataset.user_ids,
        movie_ids=dataset.movie_ids,
        interaction_nnz=int(interactions.nnz),
        item_feature_export=item_export,
        user_feature_export=user_export,
        training_data_policy=data_policy,
        training_data_policy_hash=data_policy_hash,
        package_versions=versions,
        diagnostics=diagnostics,
        verification=verification,
    )


def validate_hybrid_feature_compatibility(
    dataset: LightFMDatasetSnapshot,
    *,
    item_export: ItemFeatureExport,
    user_export: UserFeatureExport,
):
    if item_export.manifest.ontology_build_status != "success":
        raise ValueError("hybrid training requires features from a successful ontology build")
    if dataset.movie_ids != item_export.movie_ids:
        raise ValueError("dataset and ontology item feature movie mappings must match exactly")
    if dataset.user_ids != user_export.user_ids:
        raise ValueError("dataset and user feature user mappings must match exactly")
    if user_export.manifest.ontology_build_id != item_export.manifest.ontology_build_id:
        raise ValueError("user and item features reference different ontology builds")
    if user_export.manifest.ontology_source_hash != item_export.manifest.ontology_source_hash:
        raise ValueError("user and item features reference different ontology sources")
    if user_export.manifest.item_feature_export_hash != item_export.manifest.export_hash:
        raise ValueError("user feature export was built from a different item feature export")
    user_features = user_export.user_features.tocsr(copy=False).astype(np.float32)
    item_features = item_export.item_features.tocsr(copy=False).astype(np.float32)
    if user_features.shape != user_export.manifest.matrix_shape:
        raise ValueError("user feature matrix shape does not match its manifest")
    if item_features.shape != item_export.manifest.matrix_shape:
        raise ValueError("item feature matrix shape does not match its manifest")
    for label, matrix in (("user", user_features), ("item", item_features)):
        if matrix.nnz == 0 or not np.isfinite(matrix.data).all() or np.any(matrix.data <= 0):
            raise ValueError(f"hybrid {label} features must be finite positive sparse values")
    return user_features, item_features


def validate_identity_dataset(dataset: LightFMDatasetSnapshot):
    if dataset.diagnostics is None or len(dataset.diagnostics.dataset_hash) != 64:
        raise ValueError("dataset diagnostics with a SHA-256 dataset hash are required")
    if not dataset.user_ids:
        raise ValueError("identity-only training requires at least one model user")
    if not dataset.movie_ids:
        raise ValueError("identity-only training requires at least one catalog movie")

    interactions = dataset.interactions.tocoo(copy=False).astype(np.float32)
    sample_weights = dataset.sample_weights.tocoo(copy=False).astype(np.float32)
    expected_shape = (len(dataset.user_ids), len(dataset.movie_ids))
    if interactions.shape != expected_shape or sample_weights.shape != expected_shape:
        raise ValueError("interaction matrices do not match user/movie mappings")
    if interactions.nnz == 0:
        raise ValueError("identity-only training requires at least one positive interaction")
    if interactions.nnz != sample_weights.nnz or not np.array_equal(
        interactions.row, sample_weights.row
    ) or not np.array_equal(interactions.col, sample_weights.col):
        raise ValueError("interaction and sample-weight coordinates must match")
    if not np.all(interactions.data > 0) or not np.isfinite(interactions.data).all():
        raise ValueError("interactions must be finite positive values")
    if not np.all(sample_weights.data > 0) or not np.isfinite(sample_weights.data).all():
        raise ValueError("sample weights must be finite positive values")
    validate_mapping(dataset.user_ids, dataset.user_id_map, "user")
    validate_mapping(dataset.movie_ids, dataset.movie_id_map, "movie")
    return interactions, sample_weights


def apply_item_frequency_weighting(interactions, sample_weights, *, mode: str):
    if mode == "none":
        return sample_weights, {
            "mode": mode,
            "reference_user_support": None,
            "multiplier_min": 1.0,
            "multiplier_mean": 1.0,
            "multiplier_max": 1.0,
        }
    if mode != "inverse_sqrt":
        raise ValueError(f"unsupported item frequency weighting: {mode}")
    interactions = interactions.tocoo(copy=False)
    weights = sample_weights.tocoo(copy=False)
    supports = np.bincount(interactions.col, minlength=interactions.shape[1])
    positive_supports = supports[supports > 0]
    if positive_supports.size == 0:
        raise ValueError("item frequency weighting requires positive item support")
    reference = float(np.median(positive_supports))
    multipliers = np.sqrt(reference / supports[interactions.col].astype(np.float64))
    multipliers = np.clip(multipliers, 0.35, 2.0).astype(np.float32)
    adjusted = type(weights)(
        (
            weights.data.astype(np.float32, copy=False) * multipliers,
            (weights.row, weights.col),
        ),
        shape=weights.shape,
        dtype=np.float32,
    )
    return adjusted, {
        "mode": mode,
        "reference_user_support": reference,
        "multiplier_min": float(multipliers.min()),
        "multiplier_mean": float(multipliers.mean()),
        "multiplier_max": float(multipliers.max()),
    }


def validate_mapping(ids: tuple[int, ...], mapping: dict[int, int], name: str) -> None:
    if len(ids) != len(mapping):
        raise ValueError(f"{name} mapping is incomplete or contains duplicate IDs")
    for index, entity_id in enumerate(ids):
        if mapping.get(entity_id) != index:
            raise ValueError(f"{name} mapping index mismatch for ID {entity_id}")


def validate_finite_model_parameters(model) -> None:
    for name in ("user_embeddings", "user_biases", "item_embeddings", "item_biases"):
        values = getattr(model, name, None)
        if values is None or not np.isfinite(values).all():
            raise RuntimeError(f"LightFM produced invalid {name}")


def evaluate_model_health(
    model,
    *,
    user_count: int,
    movie_count: int,
    num_threads: int,
    user_features=None,
    item_features=None,
    score_centering_weight: float = 0.0,
) -> dict[str, object]:
    validate_finite_model_parameters(model)
    parameters = {
        "user_embeddings": _parameter_health(model.user_embeddings, embedding=True),
        "item_embeddings": _parameter_health(model.item_embeddings, embedding=True),
        "user_biases": _parameter_health(model.user_biases, embedding=False),
        "item_biases": _parameter_health(model.item_biases, embedding=False),
    }
    prediction = _prediction_health(
        model,
        user_count=user_count,
        movie_count=movie_count,
        num_threads=num_threads,
        user_features=user_features,
        item_features=item_features,
    )
    violations: list[str] = []
    for name in ("user_embeddings", "item_embeddings"):
        stats = parameters[name]
        if stats["max_abs"] > MODEL_HEALTH_THRESHOLDS["embedding_max_abs"]:
            violations.append(f"{name}.max_abs")
        if stats["row_norm_max"] > MODEL_HEALTH_THRESHOLDS["embedding_row_norm_max"]:
            violations.append(f"{name}.row_norm_max")
    for name in ("user_biases", "item_biases"):
        if parameters[name]["max_abs"] > MODEL_HEALTH_THRESHOLDS["bias_max_abs"]:
            violations.append(f"{name}.max_abs")
    if prediction["max_abs"] > MODEL_HEALTH_THRESHOLDS["prediction_max_abs"]:
        violations.append("prediction.max_abs")
    candidate_sample = _candidate_concentration_health(
        model,
        user_count=user_count,
        movie_count=movie_count,
        user_features=user_features,
        item_features=item_features,
        score_centering_weight=score_centering_weight,
    )
    if (
        candidate_sample["applicable"]
        and
        candidate_sample["pairwise_jaccard_mean"]
        > MODEL_HEALTH_THRESHOLDS["candidate_pairwise_jaccard_max"]
    ):
        violations.append("candidate_sample.pairwise_jaccard_mean")
    return {
        "status": "pass" if not violations else "fail",
        "thresholds": dict(MODEL_HEALTH_THRESHOLDS),
        "parameters": parameters,
        "prediction_sample": prediction,
        "candidate_sample": candidate_sample,
        "violations": violations,
    }


def assert_model_health(report: dict[str, object]) -> None:
    if report.get("status") != "pass" or report.get("violations"):
        raise ModelHealthError(report)


def assert_training_result_health(diagnostics: dict[str, object]) -> None:
    report = diagnostics.get("model_health")
    if not isinstance(report, dict):
        raise RuntimeError("LightFM artifact publication requires model health diagnostics")
    assert_model_health(report)


def _parameter_health(values, *, embedding: bool) -> dict[str, float | int]:
    array = np.asarray(values)
    if array.size == 0:
        raise RuntimeError("LightFM produced an empty parameter array")
    sample_size = min(array.shape[0], 10_000)
    sample_indices = np.unique(
        np.linspace(0, array.shape[0] - 1, num=sample_size, dtype=np.int64)
    )
    sample = np.asarray(array[sample_indices], dtype=np.float64)
    sample_abs = np.abs(sample).reshape(-1)
    sample_row_norms = (
        np.linalg.norm(sample, axis=1)
        if embedding
        else np.zeros(sample.shape[0], dtype=np.float64)
    )
    max_abs = 0.0
    row_norm_max = 0.0
    for start in range(0, array.shape[0], 16_384):
        chunk = np.asarray(array[start : start + 16_384], dtype=np.float64)
        max_abs = max(max_abs, float(np.max(np.abs(chunk), initial=0.0)))
        if embedding:
            row_norm_max = max(
                row_norm_max,
                float(np.max(np.linalg.norm(chunk, axis=1), initial=0.0)),
            )
    return {
        "count": int(array.size),
        "max_abs": max_abs,
        "sample_abs_median": float(np.median(sample_abs)),
        "sample_abs_p95": float(np.percentile(sample_abs, 95)),
        "sample_abs_p99": float(np.percentile(sample_abs, 99)),
        "row_norm_sample_median": float(np.median(sample_row_norms)),
        "row_norm_sample_p95": float(np.percentile(sample_row_norms, 95)),
        "row_norm_sample_p99": float(np.percentile(sample_row_norms, 99)),
        "row_norm_max": row_norm_max if embedding else 0.0,
    }


def _prediction_health(
    model,
    *,
    user_count: int,
    movie_count: int,
    num_threads: int,
    user_features=None,
    item_features=None,
) -> dict[str, float | int]:
    selected_users = np.unique(
        np.linspace(0, user_count - 1, num=min(user_count, 8), dtype=np.int32)
    )
    selected_items = np.unique(
        np.linspace(0, movie_count - 1, num=min(movie_count, 256), dtype=np.int32)
    )
    user_indices = np.repeat(selected_users, selected_items.size)
    item_indices = np.tile(selected_items, selected_users.size)
    scores = np.asarray(
        model.predict(
            user_indices,
            item_indices,
            user_features=user_features,
            item_features=item_features,
            num_threads=num_threads,
        ),
        dtype=np.float64,
    )
    if not np.isfinite(scores).all():
        raise RuntimeError("LightFM produced non-finite model health scores")
    absolute = np.abs(scores)
    return {
        "count": int(scores.size),
        "min": float(scores.min()),
        "median": float(np.median(scores)),
        "p95": float(np.percentile(scores, 95)),
        "p99": float(np.percentile(scores, 99)),
        "max": float(scores.max()),
        "max_abs": float(absolute.max(initial=0.0)),
    }


def _candidate_concentration_health(
    model,
    *,
    user_count: int,
    movie_count: int,
    user_features=None,
    item_features=None,
    score_centering_weight: float,
) -> dict[str, float | int]:
    selected_users = np.unique(
        np.linspace(0, user_count - 1, num=min(user_count, 8), dtype=np.int32)
    )
    if user_features is None:
        user_biases = model.user_biases[selected_users]
        user_embeddings = model.user_embeddings[selected_users]
    else:
        user_biases, user_embeddings = model.get_user_representations(
            user_features[selected_users]
        )
    user_biases = np.asarray(user_biases, dtype=np.float32)
    user_embeddings = np.asarray(user_embeddings, dtype=np.float32)
    if score_centering_weight > 0:
        if user_features is None:
            mean_user_bias = float(np.mean(model.user_biases))
            mean_user_embedding = np.mean(
                model.user_embeddings, axis=0, dtype=np.float64
            ).astype(np.float32)
        else:
            mean_user_bias, mean_user_embedding = mean_known_user_representation(
                model,
                user_features,
            )
        user_biases, user_embeddings = center_known_user_representations(
            user_biases,
            user_embeddings,
            mean_user_bias=mean_user_bias,
            mean_user_embedding=mean_user_embedding,
            weight=score_centering_weight,
        )
    top_k = min(movie_count, 100)
    top_indices = [np.empty(0, dtype=np.int64) for _ in selected_users]
    top_scores = [np.empty(0, dtype=np.float32) for _ in selected_users]
    for start in range(0, movie_count, 8_192):
        end = min(start + 8_192, movie_count)
        if item_features is None:
            item_biases = model.item_biases[start:end]
            item_embeddings = model.item_embeddings[start:end]
        else:
            item_biases, item_embeddings = model.get_item_representations(
                item_features[start:end]
            )
        scores = user_embeddings @ np.asarray(item_embeddings, dtype=np.float32).T
        scores += user_biases[:, None]
        scores += (1.0 - score_centering_weight) * np.asarray(
            item_biases, dtype=np.float32
        )[None, :]
        block_indices = np.arange(start, end, dtype=np.int64)
        for row in range(selected_users.size):
            selected = _top_k_score_indices(scores[row], top_k)
            merged_indices = np.concatenate((top_indices[row], block_indices[selected]))
            merged_scores = np.concatenate((top_scores[row], scores[row, selected]))
            keep = _top_k_score_indices(merged_scores, top_k)
            top_indices[row] = merged_indices[keep]
            top_scores[row] = merged_scores[keep]
    candidate_sets = [set(values.tolist()) for values in top_indices]
    pairwise = [
        len(left & right) / len(left | right)
        for left, right in combinations(candidate_sets, 2)
        if left or right
    ]
    return {
        "applicable": movie_count > 100,
        "user_count": int(selected_users.size),
        "top_k": top_k,
        "unique_item_count": len(set().union(*candidate_sets)),
        "pairwise_jaccard_mean": float(np.mean(pairwise)) if pairwise else 0.0,
    }


def _top_k_score_indices(scores: np.ndarray, top_k: int) -> np.ndarray:
    valid = np.flatnonzero(np.isfinite(scores))
    if valid.size > top_k:
        partition = np.argpartition(scores[valid], -top_k)[-top_k:]
        valid = valid[partition]
    return valid[np.argsort(-scores[valid], kind="stable")]


def build_prediction_verification(
    model,
    *,
    user_count: int,
    movie_count: int,
    num_threads: int,
    user_features=None,
    item_features=None,
) -> PredictionVerification:
    selected_users = np.arange(min(user_count, 3), dtype=np.int32)
    selected_items = np.unique(
        np.linspace(0, movie_count - 1, num=min(movie_count, 32), dtype=np.int32)
    )
    user_indices = np.repeat(selected_users, selected_items.size)
    item_indices = np.tile(selected_items, selected_users.size)
    scores = model.predict(
        user_indices,
        item_indices,
        user_features=user_features,
        item_features=item_features,
        num_threads=num_threads,
    )
    if not np.isfinite(scores).all():
        raise RuntimeError("LightFM produced non-finite verification scores")
    return PredictionVerification(
        user_indices=tuple(int(value) for value in user_indices),
        item_indices=tuple(int(value) for value in item_indices),
        score_hash=hash_prediction_scores(scores),
    )


def hash_prediction_scores(scores) -> str:
    canonical = np.asarray(scores, dtype="<f4")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def package_versions() -> dict[str, str]:
    packages = {
        "lightfm": "lightfm",
        "numpy": "numpy",
        "scipy": "scipy",
        "joblib": "joblib",
    }
    versions = {name: metadata.version(distribution) for name, distribution in packages.items()}
    versions["python"] = platform.python_version()
    return versions


def training_data_policy_snapshot() -> dict[str, object]:
    return {
        "positive_actions": dict(sorted(TRAINING_ACTION_WEIGHTS.items())),
        "action_priority": list(TRAINING_ACTION_PRIORITY),
        "overlap_confidence_bonus": TRAINING_OVERLAP_CONFIDENCE_BONUS,
        "max_sample_weight": TRAINING_MAX_SAMPLE_WEIGHT,
        "recency_policy": "action_specific_continuous_half_life_v1",
        "recency_half_life_days": dict(sorted(TRAINING_RECENCY_HALF_LIFE_DAYS.items())),
        "recency_min_multiplier": TRAINING_RECENCY_MIN_MULTIPLIER,
        "missing_timestamp_multipliers": dict(
            sorted(TRAINING_MISSING_TIMESTAMP_MULTIPLIERS.items())
        ),
        "passed_handling": "excluded_from_positives_and_serving_candidates",
        "social_signal_handling": "diagnostic_only",
    }


def hash_json_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
