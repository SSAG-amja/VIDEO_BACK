from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from scipy.sparse import csr_matrix, load_npz

from app.services.recsys.v3.config import ENGINE_NAME
from app.services.recsys.v3.domain.feature_registry import FEATURE_REGISTRY_VERSION
from app.services.recsys.v3.retrieval.score_calibration import mean_known_user_representation


@dataclass(frozen=True, slots=True)
class RuntimeHybridArtifact:
    path: Path
    model: Any
    user_ids: np.ndarray
    movie_ids: np.ndarray
    user_features: csr_matrix
    item_features: csr_matrix
    user_feature_tokens: tuple[str, ...]
    item_feature_tokens: tuple[str, ...]
    manifest: dict
    num_threads: int
    known_user_score_centering_weight: float
    mean_user_bias: float
    mean_user_embedding: np.ndarray

    @property
    def model_build_id(self) -> str:
        return str(self.manifest["model_build_id"])

    @property
    def ontology_build_id(self) -> int:
        return int(self.manifest["ontology"]["build_id"])

    def user_index(self, user_id: int) -> int | None:
        return _find_id(self.user_ids, user_id)

    def movie_index(self, movie_id: int) -> int | None:
        return _find_id(self.movie_ids, movie_id)


class MovieIdIndex:
    def __init__(self, movie_ids: np.ndarray):
        self._movie_ids = movie_ids

    def __contains__(self, movie_id: object) -> bool:
        return isinstance(movie_id, int) and _find_id(self._movie_ids, movie_id) is not None


def load_runtime_hybrid_artifact(path: str | Path) -> RuntimeHybridArtifact:
    artifact_dir = Path(path).resolve()
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("engine_name") != ENGINE_NAME or manifest.get("stage") != "hybrid_ontology":
        raise ValueError("serving requires a V3 hybrid ontology artifact")
    if manifest.get("feature_registry_version") != FEATURE_REGISTRY_VERSION:
        raise ValueError("serving artifact feature registry mismatch")
    ontology = manifest.get("ontology", {})
    if ontology.get("applicable") is not True or not isinstance(ontology.get("build_id"), int):
        raise ValueError("serving artifact requires an ontology build")
    _validate_files(artifact_dir, manifest.get("files", {}))

    config = _read_json(artifact_dir / "config.json")
    if config.get("stage") != "hybrid_ontology":
        raise ValueError("serving artifact config is not hybrid ontology")
    num_threads = int(config.get("num_threads", 1))
    if num_threads <= 0:
        raise ValueError("serving artifact num_threads must be positive")
    centering_weight = float(config.get("known_user_score_centering_weight", 0.0))
    if not np.isfinite(centering_weight) or not 0.0 <= centering_weight <= 1.0:
        raise ValueError("serving artifact score centering weight is invalid")

    user_ids = np.load(artifact_dir / "user_ids.npy", allow_pickle=False)
    movie_ids = np.load(artifact_dir / "movie_ids.npy", allow_pickle=False)
    user_features = load_npz(artifact_dir / "user_features.npz").tocsr()
    item_features = load_npz(artifact_dir / "item_features.npz").tocsr()
    user_tokens = tuple(joblib.load(artifact_dir / "user_feature_tokens.joblib"))
    item_tokens = tuple(joblib.load(artifact_dir / "item_feature_tokens.joblib"))
    dimensions = manifest.get("dimensions", {})
    _validate_mapping(user_ids, int(dimensions.get("users", -1)), "user")
    _validate_mapping(movie_ids, int(dimensions.get("movies", -1)), "movie")
    expected_user_shape = (len(user_ids), int(dimensions.get("user_features", -1)))
    expected_item_shape = (len(movie_ids), int(dimensions.get("item_features", -1)))
    if user_features.shape != expected_user_shape or item_features.shape != expected_item_shape:
        raise ValueError("serving artifact sparse feature dimensions mismatch")
    if len(user_tokens) != user_features.shape[1] or len(item_tokens) != item_features.shape[1]:
        raise ValueError("serving artifact feature token dimensions mismatch")
    if len(set(user_tokens)) != len(user_tokens) or len(set(item_tokens)) != len(item_tokens):
        raise ValueError("serving artifact feature tokens must be unique")
    if not np.isfinite(user_features.data).all() or not np.isfinite(item_features.data).all():
        raise ValueError("serving artifact sparse feature values must be finite")

    model = joblib.load(artifact_dir / "model.joblib")
    if model.user_embeddings.shape[0] != user_features.shape[1]:
        raise ValueError("serving model user embedding dimension mismatch")
    if model.item_embeddings.shape[0] != item_features.shape[1]:
        raise ValueError("serving model item embedding dimension mismatch")
    if centering_weight > 0:
        mean_user_bias, mean_user_embedding = mean_known_user_representation(
            model,
            user_features,
        )
    else:
        mean_user_bias = 0.0
        mean_user_embedding = np.zeros(model.user_embeddings.shape[1], dtype=np.float32)
    return RuntimeHybridArtifact(
        path=artifact_dir,
        model=model,
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_features=user_features,
        item_features=item_features,
        user_feature_tokens=user_tokens,
        item_feature_tokens=item_tokens,
        manifest=manifest,
        num_threads=num_threads,
        known_user_score_centering_weight=centering_weight,
        mean_user_bias=mean_user_bias,
        mean_user_embedding=mean_user_embedding,
    )


def _validate_files(artifact_dir: Path, files: dict) -> None:
    if not files:
        raise ValueError("serving artifact manifest has no files")
    for filename, expected in files.items():
        path = artifact_dir / filename
        if not path.is_file() or path.stat().st_size != int(expected["size_bytes"]):
            raise ValueError(f"serving artifact file is missing or changed: {filename}")
        if _hash_file(path) != expected["sha256"]:
            raise ValueError(f"serving artifact file hash mismatch: {filename}")


def _validate_mapping(values: np.ndarray, expected_size: int, label: str) -> None:
    if values.ndim != 1 or values.size != expected_size:
        raise ValueError(f"serving artifact {label} mapping dimension mismatch")
    if values.size > 1 and np.any(values[1:] <= values[:-1]):
        raise ValueError(f"serving artifact {label} IDs must be strictly increasing")


def _find_id(values: np.ndarray, value: int) -> int | None:
    position = int(np.searchsorted(values, int(value)))
    if position >= values.size or int(values[position]) != int(value):
        return None
    return position


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
