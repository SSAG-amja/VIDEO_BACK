from __future__ import annotations

import hashlib
import json
import platform
import time
from importlib import metadata

import numpy as np

from app.jobs.recsys.v3.datasets.dataset_schemas import LightFMDatasetSnapshot
from app.jobs.recsys.v3.features.feature_schemas import ItemFeatureExport, UserFeatureExport
from app.jobs.recsys.v3.training.model_schemas import (
    HybridTrainingResult,
    IdentityTrainingResult,
    LightFMTrainingConfig,
    PredictionVerification,
)
from app.services.recsys.v3.config import (
    TRAINING_ACTION_PRIORITY,
    TRAINING_ACTION_WEIGHTS,
    TRAINING_MAX_SAMPLE_WEIGHT,
    TRAINING_MISSING_TIMESTAMP_MULTIPLIER,
    TRAINING_OLDER_RECENCY_MULTIPLIER,
    TRAINING_OVERLAP_CONFIDENCE_BONUS,
    TRAINING_RECENCY_BUCKETS,
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
    validate_finite_model_parameters(model)
    verification = build_prediction_verification(
        model,
        user_count=len(dataset.user_ids),
        movie_count=len(dataset.movie_ids),
        num_threads=training_config.num_threads,
    )
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
        "package_versions": versions,
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
    validate_finite_model_parameters(model)
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
        "sample_weight": {
            "min": float(sample_weights.data.min()),
            "max": float(sample_weights.data.max()),
            "mean": float(sample_weights.data.mean()),
        },
        "package_versions": versions,
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
        "recency_buckets": [list(bucket) for bucket in TRAINING_RECENCY_BUCKETS],
        "older_recency_multiplier": TRAINING_OLDER_RECENCY_MULTIPLIER,
        "missing_timestamp_multiplier": TRAINING_MISSING_TIMESTAMP_MULTIPLIER,
        "passed_handling": "excluded_from_positives_and_serving_candidates",
        "social_signal_handling": "diagnostic_only",
    }


def hash_json_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
