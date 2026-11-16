from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.training.model_schemas import LoadedHybridArtifact
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
    population_confidence_from_diagnostics,
)


def load_training_collaborative_confidences(
    db: Session,
    artifact: LoadedHybridArtifact,
) -> dict[int, float]:
    cutoff_at = datetime.fromisoformat(str(artifact.manifest["data_cutoff_at"]))
    dataset = build_lightfm_dataset(db, data_cutoff_at=cutoff_at)
    if dataset.diagnostics.dataset_hash != artifact.manifest["dataset_hash"]:
        raise RuntimeError(
            "cannot reconstruct the model training dataset for collaborative scoring"
        )
    artifact_user_ids = tuple(int(value) for value in artifact.user_ids)
    if dataset.user_ids != artifact_user_ids:
        raise RuntimeError("model and reconstructed dataset user mappings differ")

    row_counts = np.diff(dataset.interactions.tocsr(copy=False).indptr)
    population_confidence = population_confidence_from_diagnostics(
        artifact.diagnostics,
        user_count=len(artifact_user_ids),
    )
    return {
        user_id: assess_user_collaborative_confidence(
            positive_pair_count=int(row_counts[index]),
            population_confidence=population_confidence,
        ).effective_confidence
        for index, user_id in enumerate(artifact_user_ids)
    }
