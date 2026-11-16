from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType


SUPPORTED_ENGINE_VERSIONS = ("v3", "v4")


@dataclass(frozen=True, slots=True)
class EvaluationEngineBackend:
    version: str
    config: ModuleType
    dataset_builder: ModuleType
    feature_builder: ModuleType
    feature_representation: ModuleType
    user_feature_builder: ModuleType
    artifact_publisher: ModuleType
    model_schemas: ModuleType
    trainer: ModuleType
    collaborative_confidence: ModuleType
    score_calibration: ModuleType
    model_store: ModuleType


def load_evaluation_backend(version: str) -> EvaluationEngineBackend:
    if version not in SUPPORTED_ENGINE_VERSIONS:
        raise ValueError(
            f"unsupported evaluation engine={version!r}; "
            f"expected one of {SUPPORTED_ENGINE_VERSIONS}"
        )

    jobs_root = f"app.jobs.recsys.{version}"
    services_root = f"app.services.recsys.{version}"
    return EvaluationEngineBackend(
        version=version,
        config=import_module(f"{services_root}.config"),
        dataset_builder=import_module(f"{jobs_root}.datasets.dataset_builder"),
        feature_builder=import_module(f"{jobs_root}.features.feature_builder"),
        feature_representation=import_module(
            f"{jobs_root}.features.feature_representation"
        ),
        user_feature_builder=import_module(
            f"{jobs_root}.features.user_feature_builder"
        ),
        artifact_publisher=import_module(
            f"{jobs_root}.training.artifact_publisher"
        ),
        model_schemas=import_module(f"{jobs_root}.training.model_schemas"),
        trainer=import_module(f"{jobs_root}.training.trainer"),
        collaborative_confidence=import_module(
            f"{services_root}.retrieval.collaborative_confidence"
        ),
        score_calibration=import_module(
            f"{services_root}.retrieval.score_calibration"
        ),
        model_store=import_module(f"{services_root}.serving.model_store"),
    )
