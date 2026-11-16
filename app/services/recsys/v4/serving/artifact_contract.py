from __future__ import annotations

from app.services.recsys.v4.errors import V4NotReadyError
from app.services.recsys.v4.config import ENGINE_NAME
from app.services.recsys.v4.serving.model_store import RuntimeHybridArtifact
from app.services.recsys.v4.serving.serving_bundle import (
    ServingBundle,
    get_active_serving_bundle as get_v4_active_serving_bundle,
)


def get_active_v4_serving_bundle() -> ServingBundle:
    """Load the active V4 bundle and enforce the Hybrid LightFM contract."""
    try:
        bundle = get_v4_active_serving_bundle()
    except V4NotReadyError as exc:
        raise V4NotReadyError(f"V4 serving bundle is unavailable: {exc}") from exc
    try:
        validate_v4_hybrid_artifact(bundle.model)
    except ValueError as exc:
        raise V4NotReadyError(f"V4 Hybrid LightFM artifact is invalid: {exc}") from exc
    return bundle


def validate_v4_hybrid_artifact(artifact: RuntimeHybridArtifact) -> None:
    manifest = artifact.manifest
    if manifest.get("engine_name") != ENGINE_NAME:
        raise ValueError("artifact must be owned by the V4 engine")
    if manifest.get("stage") != "hybrid_ontology":
        raise ValueError("training stage must be hybrid_ontology")

    exports = manifest.get("feature_exports")
    if not isinstance(exports, dict):
        raise ValueError("feature export contract is missing")
    for name in ("item_semantic_block_weight", "user_semantic_block_weight"):
        try:
            value = float(exports[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name} is missing") from exc
        if value <= 0:
            raise ValueError(f"{name} must be enabled")

    user_identity_count = len(artifact.user_ids)
    item_identity_count = len(artifact.movie_ids)
    if artifact.user_features.shape[1] <= user_identity_count:
        raise ValueError("user ontology feature columns are missing")
    if artifact.item_features.shape[1] <= item_identity_count:
        raise ValueError("item ontology feature columns are missing")
    if artifact.user_features[:, user_identity_count:].nnz == 0:
        raise ValueError("user ontology feature block is empty")
    if artifact.item_features[:, item_identity_count:].nnz == 0:
        raise ValueError("item ontology feature block is empty")

    user_semantic_tokens = set(artifact.user_feature_tokens[user_identity_count:])
    item_semantic_tokens = set(artifact.item_feature_tokens[item_identity_count:])
    if not user_semantic_tokens.intersection(item_semantic_tokens):
        raise ValueError("user and item ontology features do not share a semantic space")
