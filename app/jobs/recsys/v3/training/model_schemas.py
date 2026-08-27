from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.services.recsys.v3.config import (
    LIGHTFM_IDENTITY_EPOCHS,
    LIGHTFM_IDENTITY_ITEM_ALPHA,
    LIGHTFM_IDENTITY_LEARNING_RATE,
    LIGHTFM_IDENTITY_LOSS,
    LIGHTFM_IDENTITY_MAX_SAMPLED,
    LIGHTFM_IDENTITY_NO_COMPONENTS,
    LIGHTFM_IDENTITY_NUM_THREADS,
    LIGHTFM_IDENTITY_RANDOM_SEED,
    LIGHTFM_IDENTITY_USER_ALPHA,
)
from app.services.recsys.v3.domain.feature_registry import FEATURE_REGISTRY_VERSION


def feature_registry_hash_prefix() -> str:
    return hashlib.sha256(FEATURE_REGISTRY_VERSION.encode()).hexdigest()[:8]


@dataclass(frozen=True, slots=True)
class LightFMTrainingConfig:
    stage: str = "identity_only"
    no_components: int = LIGHTFM_IDENTITY_NO_COMPONENTS
    loss: str = LIGHTFM_IDENTITY_LOSS
    epochs: int = LIGHTFM_IDENTITY_EPOCHS
    learning_rate: float = LIGHTFM_IDENTITY_LEARNING_RATE
    user_alpha: float = LIGHTFM_IDENTITY_USER_ALPHA
    item_alpha: float = LIGHTFM_IDENTITY_ITEM_ALPHA
    max_sampled: int = LIGHTFM_IDENTITY_MAX_SAMPLED
    random_seed: int = LIGHTFM_IDENTITY_RANDOM_SEED
    num_threads: int = LIGHTFM_IDENTITY_NUM_THREADS

    def __post_init__(self) -> None:
        if self.stage not in {"identity_only", "hybrid_ontology"}:
            raise ValueError("LightFM training stage must be identity_only or hybrid_ontology")
        if self.loss != "warp":
            raise ValueError("V3 LightFM training requires WARP loss")
        for name, value in (
            ("no_components", self.no_components),
            ("epochs", self.epochs),
            ("max_sampled", self.max_sampled),
            ("num_threads", self.num_threads),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.random_seed < 0:
            raise ValueError("random_seed cannot be negative")
        for name, value in (
            ("learning_rate", self.learning_rate),
            ("user_alpha", self.user_alpha),
            ("item_alpha", self.item_alpha),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.learning_rate == 0:
            raise ValueError("learning_rate must be positive")

    def as_dict(self) -> dict[str, int | float | str]:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PredictionVerification:
    user_indices: tuple[int, ...]
    item_indices: tuple[int, ...]
    score_hash: str

    def __post_init__(self) -> None:
        if not self.user_indices or len(self.user_indices) != len(self.item_indices):
            raise ValueError("prediction verification requires aligned user/item indices")
        if len(self.score_hash) != 64:
            raise ValueError("prediction score hash must be a SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class IdentityTrainingResult:
    model: Any
    config: LightFMTrainingConfig
    data_cutoff_at: datetime
    dataset_hash: str
    user_ids: tuple[int, ...]
    movie_ids: tuple[int, ...]
    interaction_nnz: int
    training_data_policy: dict[str, Any]
    training_data_policy_hash: str
    package_versions: dict[str, str]
    diagnostics: dict[str, Any]
    verification: PredictionVerification

    @property
    def model_build_id(self) -> str:
        return (
            f"identity-{self.dataset_hash[:12]}-{self.config.config_hash[:12]}-"
            f"{self.training_data_policy_hash[:12]}-{feature_registry_hash_prefix()}"
        )


@dataclass(frozen=True, slots=True)
class LoadedIdentityArtifact:
    path: Any
    model: Any
    config: LightFMTrainingConfig
    user_ids: Any
    movie_ids: Any
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HybridTrainingResult:
    model: Any
    config: LightFMTrainingConfig
    data_cutoff_at: datetime
    dataset_hash: str
    user_ids: tuple[int, ...]
    movie_ids: tuple[int, ...]
    interaction_nnz: int
    item_feature_export: Any
    user_feature_export: Any
    training_data_policy: dict[str, Any]
    training_data_policy_hash: str
    package_versions: dict[str, str]
    diagnostics: dict[str, Any]
    verification: PredictionVerification

    @property
    def model_build_id(self) -> str:
        return (
            f"hybrid-{self.dataset_hash[:12]}-{self.config.config_hash[:12]}-"
            f"{self.training_data_policy_hash[:12]}-"
            f"{self.item_feature_export.manifest.export_hash[:12]}-"
            f"{self.user_feature_export.manifest.export_hash[:12]}-"
            f"{feature_registry_hash_prefix()}"
        )


@dataclass(frozen=True, slots=True)
class LoadedHybridArtifact:
    path: Any
    model: Any
    config: LightFMTrainingConfig
    user_ids: Any
    movie_ids: Any
    user_features: Any
    item_features: Any
    user_feature_tokens: tuple[str, ...]
    item_feature_tokens: tuple[str, ...]
    manifest: dict[str, Any]
