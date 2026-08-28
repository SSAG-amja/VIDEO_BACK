from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.domain.schemas import FeatureCoverageDiagnostics


@dataclass(frozen=True, slots=True)
class ItemFeaturePruningRule:
    min_movie_frequency: int = 1
    max_catalog_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.min_movie_frequency <= 0:
            raise ValueError("minimum movie frequency must be positive")
        if self.max_catalog_ratio is not None and not 0.0 < self.max_catalog_ratio <= 1.0:
            raise ValueError("maximum catalog ratio must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ItemFeatureFamilyDiagnostics:
    feature: FeatureName
    relation_type: str
    source_edge_count: int
    retained_edge_count: int
    matrix_nnz: int
    coverage: FeatureCoverageDiagnostics

    def __post_init__(self) -> None:
        for name, value in (
            ("source_edge_count", self.source_edge_count),
            ("retained_edge_count", self.retained_edge_count),
            ("matrix_nnz", self.matrix_nnz),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.retained_edge_count > self.source_edge_count:
            raise ValueError("retained edge count cannot exceed source edge count")
        if self.matrix_nnz != self.retained_edge_count:
            raise ValueError("feature matrix nnz must equal retained edge count")


@dataclass(frozen=True, slots=True)
class ItemFeatureManifest:
    exporter_version: str
    ontology_build_id: int
    ontology_engine_name: str
    ontology_schema_version: str
    ontology_source_hash: str
    movie_count: int
    feature_count: int
    matrix_nnz: int
    matrix_shape: tuple[int, int]
    movie_mapping_hash: str
    feature_mapping_hash: str
    export_hash: str
    pruning_rules: dict[str, dict[str, int | float | None]]
    family_diagnostics: tuple[ItemFeatureFamilyDiagnostics, ...]
    ontology_build_status: str = "unknown"
    representation_policy: str = "full_identity_raw"
    identity_block_weight: float = 1.0
    semantic_block_weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.exporter_version.strip() or not self.ontology_source_hash.strip():
            raise ValueError("item feature manifest versions and hashes are required")
        if self.ontology_build_id <= 0:
            raise ValueError("ontology build ID must be positive")
        if self.ontology_build_status not in {"unknown", "running", "success"}:
            raise ValueError("item feature ontology build status is invalid")
        if not self.representation_policy.strip():
            raise ValueError("item feature representation policy is required")
        for name, value in (
            ("identity_block_weight", self.identity_block_weight),
            ("semantic_block_weight", self.semantic_block_weight),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"item feature {name} must be finite and non-negative")
        if self.semantic_block_weight == 0:
            raise ValueError("item semantic block weight must be positive")
        if self.movie_count <= 0 or self.feature_count <= 0 or self.matrix_nnz <= 0:
            raise ValueError("item feature manifest counts must be positive")
        if self.matrix_shape != (self.movie_count, self.feature_count):
            raise ValueError("item feature matrix shape does not match manifest counts")
        for value in (
            self.movie_mapping_hash,
            self.feature_mapping_hash,
            self.export_hash,
        ):
            if len(value) != 64:
                raise ValueError("item feature hashes must be SHA-256 hex digests")


@dataclass(frozen=True, slots=True)
class ItemFeatureExport:
    movie_ids: tuple[int, ...]
    movie_id_map: dict[int, int]
    feature_tokens: tuple[str, ...]
    feature_token_map: dict[str, int]
    item_features: Any
    manifest: ItemFeatureManifest

    def __post_init__(self) -> None:
        if len(self.movie_ids) != len(self.movie_id_map):
            raise ValueError("movie ID mapping is incomplete")
        if len(self.feature_tokens) != len(self.feature_token_map):
            raise ValueError("feature token mapping is incomplete")
        if self.item_features.shape != self.manifest.matrix_shape:
            raise ValueError("item feature matrix shape does not match manifest")
        if int(self.item_features.nnz) != self.manifest.matrix_nnz:
            raise ValueError("item feature matrix nnz does not match manifest")


@dataclass(frozen=True, slots=True)
class UserFeatureManifest:
    exporter_version: str
    ontology_build_id: int
    ontology_source_hash: str
    item_feature_export_hash: str
    user_count: int
    feature_count: int
    matrix_nnz: int
    matrix_shape: tuple[int, int]
    user_mapping_hash: str
    feature_mapping_hash: str
    export_hash: str
    explicit_genre_pair_count: int
    favorite_movie_pair_count: int
    favorite_derived_pair_count: int
    covered_user_count: int
    missing_favorite_movie_count: int
    feature_family_counts: dict[str, int]
    explicit_genre_weight: float = 1.0
    favorite_derived_weight: float = 0.5
    vocabulary_policy: str = "identity_all_genres_observed_favorite_features"
    representation_policy: str = "full_identity_raw"
    identity_block_weight: float = 1.0
    semantic_block_weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.exporter_version.strip() or not self.ontology_source_hash.strip():
            raise ValueError("user feature manifest versions and hashes are required")
        if self.ontology_build_id <= 0:
            raise ValueError("user feature ontology build ID must be positive")
        if self.user_count <= 0 or self.feature_count <= 0 or self.matrix_nnz <= 0:
            raise ValueError("user feature manifest counts must be positive")
        if self.matrix_shape != (self.user_count, self.feature_count):
            raise ValueError("user feature matrix shape does not match manifest")
        for name, value in (
            ("explicit_genre_pair_count", self.explicit_genre_pair_count),
            ("favorite_movie_pair_count", self.favorite_movie_pair_count),
            ("favorite_derived_pair_count", self.favorite_derived_pair_count),
            ("covered_user_count", self.covered_user_count),
            ("missing_favorite_movie_count", self.missing_favorite_movie_count),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.covered_user_count > self.user_count:
            raise ValueError("user feature coverage cannot exceed user count")
        for name, value in (
            ("explicit_genre_weight", self.explicit_genre_weight),
            ("favorite_derived_weight", self.favorite_derived_weight),
        ):
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        if not self.vocabulary_policy.strip():
            raise ValueError("user feature vocabulary policy is required")
        if not self.representation_policy.strip():
            raise ValueError("user feature representation policy is required")
        for name, value in (
            ("identity_block_weight", self.identity_block_weight),
            ("semantic_block_weight", self.semantic_block_weight),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"user feature {name} must be finite and positive")
        for value in (
            self.item_feature_export_hash,
            self.user_mapping_hash,
            self.feature_mapping_hash,
            self.export_hash,
        ):
            if len(value) != 64:
                raise ValueError("user feature hashes must be SHA-256 hex digests")


@dataclass(frozen=True, slots=True)
class UserFeatureExport:
    user_ids: tuple[int, ...]
    user_id_map: dict[int, int]
    feature_tokens: tuple[str, ...]
    feature_token_map: dict[str, int]
    user_features: Any
    manifest: UserFeatureManifest

    def __post_init__(self) -> None:
        if len(self.user_ids) != len(self.user_id_map):
            raise ValueError("user ID mapping is incomplete")
        if len(self.feature_tokens) != len(self.feature_token_map):
            raise ValueError("user feature token mapping is incomplete")
        if self.user_features.shape != self.manifest.matrix_shape:
            raise ValueError("user feature matrix shape does not match manifest")
        if int(self.user_features.nnz) != self.manifest.matrix_nnz:
            raise ValueError("user feature matrix nnz does not match manifest")
