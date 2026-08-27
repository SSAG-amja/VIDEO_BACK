from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix

from app.jobs.recsys.v3.training.artifact_publisher import (
    load_identity_artifact,
    publish_identity_artifact,
)
from app.jobs.recsys.v3.datasets.dataset_schemas import DatasetDiagnostics, LightFMDatasetSnapshot
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_identity_model


def synthetic_dataset(*, empty: bool = False) -> LightFMDatasetSnapshot:
    user_ids = () if empty else (101, 202, 303)
    movie_ids = (10, 20, 30, 40, 50)
    rows = np.array([], dtype=np.int32) if empty else np.array([0, 0, 1, 1, 2, 2])
    columns = np.array([], dtype=np.int32) if empty else np.array([0, 1, 1, 2, 2, 3])
    values = np.ones(rows.size, dtype=np.float32)
    weights = np.array([], dtype=np.float32) if empty else np.array(
        [1.0, 2.0, 1.5, 2.0, 1.0, 2.3], dtype=np.float32
    )
    shape = (len(user_ids), len(movie_ids))
    cutoff = datetime(2026, 8, 20, 12, 0, 0)
    diagnostics = DatasetDiagnostics(
        data_cutoff_at=cutoff,
        catalog_movie_count=len(movie_ids),
        model_user_count=len(user_ids),
        positive_pair_count=rows.size,
        raw_signal_count=rows.size,
        action_signal_counts={},
        passed_movie_count=0,
        watched_movie_count=0,
        excluded_pair_count=0,
        passed_positive_conflict_count=0,
        missing_timestamp_count=0,
        dataset_hash="a" * 64,
    )
    return LightFMDatasetSnapshot(
        data_cutoff_at=cutoff,
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_id_map={value: index for index, value in enumerate(user_ids)},
        movie_id_map={value: index for index, value in enumerate(movie_ids)},
        interactions=coo_matrix((values, (rows, columns)), shape=shape, dtype=np.float32),
        sample_weights=coo_matrix((weights, (rows, columns)), shape=shape, dtype=np.float32),
        positives=(),
        diagnostics=diagnostics,
    )


class IdentityTrainerTest(unittest.TestCase):
    def test_train_publish_reload_preserves_predictions(self) -> None:
        config = LightFMTrainingConfig(no_components=4, epochs=3, num_threads=1)
        result = train_identity_model(synthetic_dataset(), config)

        with tempfile.TemporaryDirectory(prefix="v3-identity-test-") as temporary:
            artifact_path = publish_identity_artifact(result, temporary)
            loaded = load_identity_artifact(artifact_path)

            self.assertEqual(loaded.manifest["model_build_id"], result.model_build_id)
            self.assertEqual(loaded.manifest["ontology"]["applicable"], False)
            self.assertEqual(
                loaded.manifest["training_data_policy_hash"],
                result.training_data_policy_hash,
            )
            self.assertEqual(loaded.user_ids.tolist(), [101, 202, 303])
            self.assertEqual(loaded.movie_ids.tolist(), [10, 20, 30, 40, 50])
            diagnostics = json.loads((artifact_path / "diagnostics.json").read_text())
            self.assertTrue(diagnostics["artifact_reload_exact_match"])

            with self.assertRaises(FileExistsError):
                publish_identity_artifact(result, temporary)

    def test_empty_behavior_dataset_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one model user"):
            train_identity_model(
                synthetic_dataset(empty=True),
                LightFMTrainingConfig(no_components=4, epochs=1),
            )

    def test_invalid_baseline_config_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "WARP"):
            LightFMTrainingConfig(loss="bpr")
        with self.assertRaisesRegex(ValueError, "num_threads"):
            LightFMTrainingConfig(num_threads=0)

    def test_artifact_hash_detects_tampering(self) -> None:
        result = train_identity_model(
            synthetic_dataset(),
            LightFMTrainingConfig(no_components=4, epochs=1),
        )
        with tempfile.TemporaryDirectory(prefix="v3-identity-test-") as temporary:
            artifact_path = publish_identity_artifact(result, temporary)
            config_path = Path(artifact_path) / "config.json"
            config_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
                load_identity_artifact(artifact_path)


if __name__ == "__main__":
    unittest.main()
