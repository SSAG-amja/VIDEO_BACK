from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from scipy.sparse import load_npz, save_npz

from app.jobs.recsys.v3.training.model_schemas import (
    feature_registry_hash_prefix,
    HybridTrainingResult,
    IdentityTrainingResult,
    LightFMTrainingConfig,
    LoadedIdentityArtifact,
    LoadedHybridArtifact,
)
from app.jobs.recsys.v3.training.trainer import hash_json_payload, hash_prediction_scores
from app.jobs.recsys.v3.features.user_feature_builder import (
    hash_ordered_values,
    hash_user_feature_export,
)
from app.services.recsys.v3.config import ENGINE_NAME, ENGINE_VERSION
from app.services.recsys.v3.domain.feature_registry import FEATURE_REGISTRY_VERSION


ARTIFACT_FORMAT_VERSION = 1
ARTIFACT_FILES = (
    "model.joblib",
    "user_ids.npy",
    "movie_ids.npy",
    "config.json",
    "diagnostics.json",
)
HYBRID_ARTIFACT_FILES = (
    "model.joblib",
    "user_ids.npy",
    "movie_ids.npy",
    "user_features.npz",
    "item_features.npz",
    "user_feature_tokens.joblib",
    "item_feature_tokens.joblib",
    "user_feature_manifest.json",
    "item_feature_manifest.json",
    "config.json",
    "diagnostics.json",
)


def publish_identity_artifact(
    result: IdentityTrainingResult,
    output_root: str | Path = "assets/ml_models/v3",
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / result.model_build_id
    if target.exists():
        raise FileExistsError(f"immutable model artifact already exists: {target}")

    lock_path = root / f".{result.model_build_id}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(f"model artifact publication is already running: {target}") from exc

    temporary: Path | None = None
    try:
        if target.exists():
            raise FileExistsError(f"immutable model artifact already exists: {target}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{result.model_build_id}-", dir=root))
        joblib.dump(result.model, temporary / "model.joblib", compress=3)
        np.save(temporary / "user_ids.npy", np.asarray(result.user_ids, dtype=np.int64))
        np.save(temporary / "movie_ids.npy", np.asarray(result.movie_ids, dtype=np.int64))
        write_json(temporary / "config.json", result.config.as_dict())
        diagnostics = dict(result.diagnostics)
        diagnostics["artifact_reload_exact_match"] = True
        write_json(temporary / "diagnostics.json", diagnostics)

        manifest = build_manifest(result, temporary)
        write_json(temporary / "manifest.json", manifest)
        load_identity_artifact(temporary)
        temporary.rename(target)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
    return target


def build_manifest(result: IdentityTrainingResult, artifact_dir: Path) -> dict[str, Any]:
    return {
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "model_build_id": result.model_build_id,
        "engine_name": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "stage": result.config.stage,
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_cutoff_at": result.data_cutoff_at.isoformat(),
        "dataset_hash": result.dataset_hash,
        "training_config_hash": result.config.config_hash,
        "training_data_policy": result.training_data_policy,
        "training_data_policy_hash": result.training_data_policy_hash,
        "ontology": {
            "build_id": None,
            "schema_version": None,
            "source_hash": None,
            "applicable": False,
        },
        "dimensions": {
            "users": len(result.user_ids),
            "movies": len(result.movie_ids),
            "interactions": result.interaction_nnz,
            "user_features": len(result.user_ids),
            "item_features": len(result.movie_ids),
        },
        "package_versions": result.package_versions,
        "verification": {
            "user_indices": list(result.verification.user_indices),
            "item_indices": list(result.verification.item_indices),
            "prediction_count": len(result.verification.user_indices),
            "score_hash": result.verification.score_hash,
        },
        "files": {
            filename: {
                "sha256": hash_file(artifact_dir / filename),
                "size_bytes": (artifact_dir / filename).stat().st_size,
            }
            for filename in ARTIFACT_FILES
        },
    }


def load_identity_artifact(path: str | Path) -> LoadedIdentityArtifact:
    artifact_dir = Path(path)
    manifest = read_json(artifact_dir / "manifest.json")
    validate_manifest(manifest, artifact_dir)
    config = LightFMTrainingConfig(**read_json(artifact_dir / "config.json"))
    if config.config_hash != manifest["training_config_hash"]:
        raise ValueError("artifact training config hash mismatch")
    expected_build_id = (
        f"identity-{manifest['dataset_hash'][:12]}-{config.config_hash[:12]}-"
        f"{manifest['training_data_policy_hash'][:12]}-{feature_registry_hash_prefix()}"
    )
    if manifest.get("model_build_id") != expected_build_id:
        raise ValueError("artifact model build ID does not match its inputs")

    user_ids = np.load(artifact_dir / "user_ids.npy", allow_pickle=False)
    movie_ids = np.load(artifact_dir / "movie_ids.npy", allow_pickle=False)
    dimensions = manifest["dimensions"]
    if user_ids.ndim != 1 or user_ids.size != dimensions["users"]:
        raise ValueError("artifact user mapping dimension mismatch")
    if movie_ids.ndim != 1 or movie_ids.size != dimensions["movies"]:
        raise ValueError("artifact movie mapping dimension mismatch")
    if (user_ids.size > 1 and np.any(user_ids[1:] <= user_ids[:-1])) or (
        movie_ids.size > 1 and np.any(movie_ids[1:] <= movie_ids[:-1])
    ):
        raise ValueError("artifact mappings must contain strictly increasing unique IDs")

    model = joblib.load(artifact_dir / "model.joblib")
    if model.user_embeddings.shape[0] != user_ids.size:
        raise ValueError("model user embedding dimension mismatch")
    if model.item_embeddings.shape[0] != movie_ids.size:
        raise ValueError("model item embedding dimension mismatch")
    verify_reloaded_predictions(model, manifest, num_threads=config.num_threads)
    return LoadedIdentityArtifact(
        path=artifact_dir,
        model=model,
        config=config,
        user_ids=user_ids,
        movie_ids=movie_ids,
        manifest=manifest,
    )


def publish_hybrid_artifact(
    result: HybridTrainingResult,
    output_root: str | Path = "assets/ml_models/v3",
) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / result.model_build_id
    if target.exists():
        raise FileExistsError(f"immutable model artifact already exists: {target}")

    lock_path = root / f".{result.model_build_id}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(f"model artifact publication is already running: {target}") from exc

    temporary: Path | None = None
    try:
        if target.exists():
            raise FileExistsError(f"immutable model artifact already exists: {target}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{result.model_build_id}-", dir=root))
        joblib.dump(result.model, temporary / "model.joblib", compress=3)
        np.save(temporary / "user_ids.npy", np.asarray(result.user_ids, dtype=np.int64))
        np.save(temporary / "movie_ids.npy", np.asarray(result.movie_ids, dtype=np.int64))
        save_npz(
            temporary / "user_features.npz",
            result.user_feature_export.user_features.tocsr(copy=False),
            compressed=True,
        )
        save_npz(
            temporary / "item_features.npz",
            result.item_feature_export.item_features.tocsr(copy=False),
            compressed=True,
        )
        joblib.dump(
            result.user_feature_export.feature_tokens,
            temporary / "user_feature_tokens.joblib",
            compress=3,
        )
        joblib.dump(
            result.item_feature_export.feature_tokens,
            temporary / "item_feature_tokens.joblib",
            compress=3,
        )
        write_json(
            temporary / "user_feature_manifest.json",
            asdict(result.user_feature_export.manifest),
        )
        write_json(
            temporary / "item_feature_manifest.json",
            asdict(result.item_feature_export.manifest),
        )
        write_json(temporary / "config.json", result.config.as_dict())
        diagnostics = dict(result.diagnostics)
        diagnostics["artifact_reload_exact_match"] = True
        write_json(temporary / "diagnostics.json", diagnostics)

        manifest = build_hybrid_manifest(result, temporary)
        write_json(temporary / "manifest.json", manifest)
        load_hybrid_artifact(temporary)
        temporary.rename(target)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
    return target


def build_hybrid_manifest(
    result: HybridTrainingResult,
    artifact_dir: Path,
) -> dict[str, Any]:
    item_manifest = result.item_feature_export.manifest
    user_manifest = result.user_feature_export.manifest
    return {
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "model_build_id": result.model_build_id,
        "engine_name": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "stage": result.config.stage,
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_cutoff_at": result.data_cutoff_at.isoformat(),
        "dataset_hash": result.dataset_hash,
        "training_config_hash": result.config.config_hash,
        "training_data_policy": result.training_data_policy,
        "training_data_policy_hash": result.training_data_policy_hash,
        "ontology": {
            "build_id": item_manifest.ontology_build_id,
            "schema_version": item_manifest.ontology_schema_version,
            "source_hash": item_manifest.ontology_source_hash,
            "applicable": True,
        },
        "feature_exports": {
            "item_exporter_version": item_manifest.exporter_version,
            "item_export_hash": item_manifest.export_hash,
            "item_movie_mapping_hash": item_manifest.movie_mapping_hash,
            "item_feature_mapping_hash": item_manifest.feature_mapping_hash,
            "user_exporter_version": user_manifest.exporter_version,
            "user_export_hash": user_manifest.export_hash,
            "user_mapping_hash": user_manifest.user_mapping_hash,
            "user_feature_mapping_hash": user_manifest.feature_mapping_hash,
            "user_parent_item_export_hash": user_manifest.item_feature_export_hash,
        },
        "dimensions": {
            "users": len(result.user_ids),
            "movies": len(result.movie_ids),
            "interactions": result.interaction_nnz,
            "user_features": user_manifest.feature_count,
            "user_feature_nnz": user_manifest.matrix_nnz,
            "item_features": item_manifest.feature_count,
            "item_feature_nnz": item_manifest.matrix_nnz,
        },
        "package_versions": result.package_versions,
        "verification": {
            "user_indices": list(result.verification.user_indices),
            "item_indices": list(result.verification.item_indices),
            "prediction_count": len(result.verification.user_indices),
            "score_hash": result.verification.score_hash,
        },
        "files": {
            filename: {
                "sha256": hash_file(artifact_dir / filename),
                "size_bytes": (artifact_dir / filename).stat().st_size,
            }
            for filename in HYBRID_ARTIFACT_FILES
        },
    }


def load_hybrid_artifact(path: str | Path) -> LoadedHybridArtifact:
    artifact_dir = Path(path)
    manifest = read_json(artifact_dir / "manifest.json")
    validate_hybrid_manifest(manifest, artifact_dir)
    config = LightFMTrainingConfig(**read_json(artifact_dir / "config.json"))
    if config.stage != "hybrid_ontology" or config.config_hash != manifest["training_config_hash"]:
        raise ValueError("hybrid artifact training config mismatch")
    exports = manifest["feature_exports"]
    expected_build_id = (
        f"hybrid-{manifest['dataset_hash'][:12]}-{config.config_hash[:12]}-"
        f"{manifest['training_data_policy_hash'][:12]}-"
        f"{exports['item_export_hash'][:12]}-{exports['user_export_hash'][:12]}-"
        f"{feature_registry_hash_prefix()}"
    )
    if manifest.get("model_build_id") != expected_build_id:
        raise ValueError("hybrid artifact model build ID does not match its inputs")

    user_ids = np.load(artifact_dir / "user_ids.npy", allow_pickle=False)
    movie_ids = np.load(artifact_dir / "movie_ids.npy", allow_pickle=False)
    user_features = load_npz(artifact_dir / "user_features.npz").tocsr()
    item_features = load_npz(artifact_dir / "item_features.npz").tocsr()
    user_tokens = tuple(joblib.load(artifact_dir / "user_feature_tokens.joblib"))
    item_tokens = tuple(joblib.load(artifact_dir / "item_feature_tokens.joblib"))
    user_feature_manifest = read_json(artifact_dir / "user_feature_manifest.json")
    item_feature_manifest = read_json(artifact_dir / "item_feature_manifest.json")
    dimensions = manifest["dimensions"]
    validate_hybrid_mappings(
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_tokens=user_tokens,
        item_tokens=item_tokens,
        dimensions=dimensions,
    )
    if user_features.shape != (dimensions["users"], dimensions["user_features"]):
        raise ValueError("hybrid artifact user feature matrix dimension mismatch")
    if item_features.shape != (dimensions["movies"], dimensions["item_features"]):
        raise ValueError("hybrid artifact item feature matrix dimension mismatch")
    if user_features.nnz != dimensions["user_feature_nnz"]:
        raise ValueError("hybrid artifact user feature nnz mismatch")
    if item_features.nnz != dimensions["item_feature_nnz"]:
        raise ValueError("hybrid artifact item feature nnz mismatch")
    validate_sparse_feature_values(user_features, "user")
    validate_sparse_feature_values(item_features, "item")
    validate_hybrid_feature_manifests(
        manifest,
        user_feature_manifest=user_feature_manifest,
        item_feature_manifest=item_feature_manifest,
    )
    if hash_ordered_values("user", (str(int(value)) for value in user_ids)) != exports[
        "user_mapping_hash"
    ]:
        raise ValueError("hybrid artifact user mapping hash mismatch")
    if hash_ordered_values("movie", (str(int(value)) for value in movie_ids)) != exports[
        "item_movie_mapping_hash"
    ]:
        raise ValueError("hybrid artifact movie mapping hash mismatch")
    if hash_ordered_values("user_feature", user_tokens) != exports[
        "user_feature_mapping_hash"
    ]:
        raise ValueError("hybrid artifact user feature mapping hash mismatch")
    if hash_ordered_values("feature", item_tokens) != exports["item_feature_mapping_hash"]:
        raise ValueError("hybrid artifact item feature mapping hash mismatch")
    if hash_user_feature_export(
        user_mapping_hash=exports["user_mapping_hash"],
        feature_mapping_hash=exports["user_feature_mapping_hash"],
        item_feature_export_hash=exports["item_export_hash"],
        matrix=user_features,
    ) != exports["user_export_hash"]:
        raise ValueError("hybrid artifact user feature export content hash mismatch")

    model = joblib.load(artifact_dir / "model.joblib")
    if model.user_embeddings.shape[0] != dimensions["user_features"]:
        raise ValueError("hybrid model user embedding dimension mismatch")
    if model.item_embeddings.shape[0] != dimensions["item_features"]:
        raise ValueError("hybrid model item embedding dimension mismatch")
    verify_reloaded_predictions(
        model,
        manifest,
        num_threads=config.num_threads,
        user_features=user_features,
        item_features=item_features,
    )
    return LoadedHybridArtifact(
        path=artifact_dir,
        model=model,
        config=config,
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_features=user_features,
        item_features=item_features,
        user_feature_tokens=user_tokens,
        item_feature_tokens=item_tokens,
        manifest=manifest,
    )


def validate_hybrid_manifest(manifest: dict[str, Any], artifact_dir: Path) -> None:
    if manifest.get("artifact_format_version") != ARTIFACT_FORMAT_VERSION:
        raise ValueError("unsupported hybrid model artifact format")
    if manifest.get("engine_name") != ENGINE_NAME or manifest.get("stage") != "hybrid_ontology":
        raise ValueError("artifact is not a V3 ontology hybrid model")
    if manifest.get("feature_registry_version") != FEATURE_REGISTRY_VERSION:
        raise ValueError("hybrid artifact feature registry version mismatch")
    if len(str(manifest.get("dataset_hash", ""))) != 64:
        raise ValueError("hybrid artifact dataset hash is invalid")
    if len(str(manifest.get("training_data_policy_hash", ""))) != 64:
        raise ValueError("hybrid artifact training data policy hash is invalid")
    data_policy = manifest.get("training_data_policy")
    if not isinstance(data_policy, dict) or hash_json_payload(data_policy) != manifest.get(
        "training_data_policy_hash"
    ):
        raise ValueError("hybrid artifact training data policy hash mismatch")
    ontology = manifest.get("ontology", {})
    if ontology.get("applicable") is not True or not isinstance(ontology.get("build_id"), int):
        raise ValueError("hybrid artifact requires an ontology build")
    validate_artifact_files(manifest, artifact_dir, HYBRID_ARTIFACT_FILES)


def validate_hybrid_mappings(
    *,
    user_ids,
    movie_ids,
    user_tokens: tuple[str, ...],
    item_tokens: tuple[str, ...],
    dimensions: dict[str, int],
) -> None:
    if user_ids.ndim != 1 or user_ids.size != dimensions["users"]:
        raise ValueError("hybrid artifact user mapping dimension mismatch")
    if movie_ids.ndim != 1 or movie_ids.size != dimensions["movies"]:
        raise ValueError("hybrid artifact movie mapping dimension mismatch")
    if (user_ids.size > 1 and np.any(user_ids[1:] <= user_ids[:-1])) or (
        movie_ids.size > 1 and np.any(movie_ids[1:] <= movie_ids[:-1])
    ):
        raise ValueError("hybrid artifact entity mappings must be strictly increasing")
    if len(user_tokens) != dimensions["user_features"] or any(
        not isinstance(token, str) or not token for token in user_tokens
    ):
        raise ValueError("hybrid artifact user feature token mapping mismatch")
    if len(item_tokens) != dimensions["item_features"] or any(
        not isinstance(token, str) or not token for token in item_tokens
    ):
        raise ValueError("hybrid artifact item feature token mapping mismatch")
    if any(current == following for current, following in zip(user_tokens, user_tokens[1:])):
        raise ValueError("hybrid artifact user feature tokens contain adjacent duplicates")
    if any(current == following for current, following in zip(item_tokens, item_tokens[1:])):
        raise ValueError("hybrid artifact item feature tokens contain adjacent duplicates")
    if any(
        user_tokens[index] != f"user:{int(user_id)}"
        for index, user_id in enumerate(user_ids)
    ):
        raise ValueError("hybrid artifact user identity feature mapping mismatch")
    if any(
        item_tokens[index] != f"movie:{int(movie_id)}"
        for index, movie_id in enumerate(movie_ids)
    ):
        raise ValueError("hybrid artifact movie identity feature mapping mismatch")


def validate_hybrid_feature_manifests(
    manifest: dict[str, Any],
    *,
    user_feature_manifest: dict[str, Any],
    item_feature_manifest: dict[str, Any],
) -> None:
    exports = manifest["feature_exports"]
    ontology = manifest["ontology"]
    if item_feature_manifest.get("ontology_build_status") != "success":
        raise ValueError("hybrid artifact item features require a successful ontology build")
    if item_feature_manifest.get("export_hash") != exports["item_export_hash"]:
        raise ValueError("hybrid artifact item feature export hash mismatch")
    if user_feature_manifest.get("export_hash") != exports["user_export_hash"]:
        raise ValueError("hybrid artifact user feature export hash mismatch")
    if user_feature_manifest.get("item_feature_export_hash") != exports["item_export_hash"]:
        raise ValueError("hybrid user features reference a different item export")
    if item_feature_manifest.get("movie_mapping_hash") != exports["item_movie_mapping_hash"]:
        raise ValueError("hybrid item movie mapping manifest mismatch")
    if item_feature_manifest.get("feature_mapping_hash") != exports["item_feature_mapping_hash"]:
        raise ValueError("hybrid item feature mapping manifest mismatch")
    if user_feature_manifest.get("user_mapping_hash") != exports["user_mapping_hash"]:
        raise ValueError("hybrid user mapping manifest mismatch")
    if user_feature_manifest.get("feature_mapping_hash") != exports["user_feature_mapping_hash"]:
        raise ValueError("hybrid user feature mapping manifest mismatch")
    if item_feature_manifest.get("ontology_schema_version") != ontology["schema_version"]:
        raise ValueError("hybrid item feature ontology schema mismatch")
    for feature_manifest in (user_feature_manifest, item_feature_manifest):
        if feature_manifest.get("ontology_build_id") != ontology["build_id"]:
            raise ValueError("hybrid feature manifest ontology build mismatch")
        if feature_manifest.get("ontology_source_hash") != ontology["source_hash"]:
            raise ValueError("hybrid feature manifest ontology source mismatch")


def validate_sparse_feature_values(matrix, label: str) -> None:
    if matrix.nnz == 0 or not np.isfinite(matrix.data).all() or np.any(matrix.data <= 0):
        raise ValueError(f"hybrid artifact {label} features must be finite and positive")


def validate_manifest(manifest: dict[str, Any], artifact_dir: Path) -> None:
    if manifest.get("artifact_format_version") != ARTIFACT_FORMAT_VERSION:
        raise ValueError("unsupported model artifact format")
    if manifest.get("engine_name") != ENGINE_NAME or manifest.get("stage") != "identity_only":
        raise ValueError("artifact is not a V3 identity-only model")
    if manifest.get("feature_registry_version") != FEATURE_REGISTRY_VERSION:
        raise ValueError("identity artifact feature registry version mismatch")
    if len(str(manifest.get("dataset_hash", ""))) != 64:
        raise ValueError("artifact dataset hash is invalid")
    if len(str(manifest.get("training_data_policy_hash", ""))) != 64:
        raise ValueError("artifact training data policy hash is invalid")
    data_policy = manifest.get("training_data_policy")
    if not isinstance(data_policy, dict) or hash_json_payload(data_policy) != manifest.get(
        "training_data_policy_hash"
    ):
        raise ValueError("artifact training data policy hash mismatch")
    ontology = manifest.get("ontology", {})
    if ontology.get("applicable") is not False or ontology.get("build_id") is not None:
        raise ValueError("identity-only artifact cannot depend on an ontology build")
    validate_artifact_files(manifest, artifact_dir, ARTIFACT_FILES)


def validate_artifact_files(
    manifest: dict[str, Any],
    artifact_dir: Path,
    expected_files: tuple[str, ...],
) -> None:
    files = manifest.get("files", {})
    if set(files) != set(expected_files):
        raise ValueError("artifact file manifest is incomplete")
    for filename in expected_files:
        file_path = artifact_dir / filename
        if not file_path.is_file():
            raise ValueError(f"artifact file is missing: {filename}")
        metadata = files[filename]
        if file_path.stat().st_size != metadata.get("size_bytes"):
            raise ValueError(f"artifact file size mismatch: {filename}")
        if hash_file(file_path) != metadata.get("sha256"):
            raise ValueError(f"artifact file hash mismatch: {filename}")


def verify_reloaded_predictions(
    model,
    manifest: dict[str, Any],
    *,
    num_threads: int,
    user_features=None,
    item_features=None,
) -> None:
    verification = manifest.get("verification", {})
    user_indices = np.asarray(verification.get("user_indices", ()), dtype=np.int32)
    item_indices = np.asarray(verification.get("item_indices", ()), dtype=np.int32)
    if user_indices.size == 0 or user_indices.shape != item_indices.shape:
        raise ValueError("artifact prediction verification indices are invalid")
    scores = model.predict(
        user_indices,
        item_indices,
        user_features=user_features,
        item_features=item_features,
        num_threads=num_threads,
    )
    if not np.isfinite(scores).all():
        raise ValueError("reloaded model produced non-finite scores")
    if hash_prediction_scores(scores) != verification.get("score_hash"):
        raise ValueError("model predictions changed after artifact reload")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path.name}")
    return payload


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
