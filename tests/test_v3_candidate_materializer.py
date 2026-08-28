from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

import numpy as np

from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact, publish_hybrid_artifact
from app.jobs.recsys.v3.candidates.candidate_materializer import materialize_candidate_batch
from app.jobs.recsys.v3.candidates.candidate_publisher import publish_candidate_snapshot
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig
from app.jobs.recsys.v3.candidates.candidate_snapshot import (
    iter_candidate_snapshot_batches,
    load_candidate_snapshot,
    materialize_candidate_snapshot,
)
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_hybrid_model
from tests.test_v3_hybrid_trainer import synthetic_item_export, synthetic_user_export
from tests.test_v3_identity_trainer import synthetic_dataset


class _ZeroRepresentationModel:
    def get_user_representations(self, features=None):
        return np.zeros(features.shape[0], dtype=np.float32), np.zeros((features.shape[0], 2), dtype=np.float32)

    def get_item_representations(self, features=None):
        return np.zeros(features.shape[0], dtype=np.float32), np.zeros((features.shape[0], 2), dtype=np.float32)


class _OneUserFailureModel:
    def __init__(self, delegate, failing_identity_column: int):
        self.delegate = delegate
        self.failing_identity_column = failing_identity_column

    def get_user_representations(self, features=None):
        if features[:, self.failing_identity_column].nnz:
            raise RuntimeError("synthetic user representation failure")
        return self.delegate.get_user_representations(features)

    def get_item_representations(self, features=None):
        return self.delegate.get_item_representations(features)


class _RecordingSession:
    def __init__(self):
        self.calls = []
        self.flushed = False

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))

    def flush(self):
        self.flushed = True


class CandidateMaterializerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        item_export = synthetic_item_export()
        user_export = synthetic_user_export(item_export)
        cls.result = train_hybrid_model(
            synthetic_dataset(),
            item_export=item_export,
            user_export=user_export,
            config=LightFMTrainingConfig(
                stage="hybrid_ontology",
                no_components=4,
                epochs=3,
                num_threads=1,
            ),
        )
        cls.artifact_dir = tempfile.TemporaryDirectory(prefix="v3-candidate-model-")
        artifact_path = publish_hybrid_artifact(cls.result, cls.artifact_dir.name)
        cls.artifact = load_hybrid_artifact(artifact_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.artifact_dir.cleanup()

    def test_blockwise_top_k_matches_full_lightfm_prediction_and_excludes_movies(self) -> None:
        config = CandidateMaterializationConfig(
            top_k=3,
            user_block_size=2,
            item_block_size=2,
            checkpoint_user_count=2,
        )
        exclusions = {101: {10, 20}, 202: {30}}
        batch = materialize_candidate_batch(
            self.artifact,
            [0, 1, 2],
            exclusions_by_user_id=exclusions,
            config=config,
        )

        self.assertEqual(batch.failures, ())
        self.assertLessEqual(batch.peak_score_block_bytes, 2 * 2 * np.dtype(np.float32).itemsize)
        for user_index, user_id in enumerate(self.artifact.user_ids):
            item_indices = np.arange(len(self.artifact.movie_ids), dtype=np.int32)
            scores = self.artifact.model.predict(
                np.full(item_indices.size, user_index, dtype=np.int32),
                item_indices,
                user_features=self.artifact.user_features,
                item_features=self.artifact.item_features,
                num_threads=1,
            )
            allowed = [
                index
                for index, movie_id in enumerate(self.artifact.movie_ids)
                if int(movie_id) not in exclusions.get(int(user_id), set())
            ]
            expected = sorted(
                allowed,
                key=lambda index: (-float(scores[index]), int(self.artifact.movie_ids[index])),
            )[:3]
            mask = batch.candidate_user_ids == int(user_id)
            self.assertEqual(
                batch.movie_ids[mask].tolist(),
                [int(self.artifact.movie_ids[index]) for index in expected],
            )
            np.testing.assert_allclose(batch.model_scores[mask], scores[expected], rtol=1e-6, atol=1e-6)
            self.assertEqual(batch.source_ranks[mask].tolist(), list(range(1, len(expected) + 1)))

    def test_equal_scores_are_ranked_by_movie_id(self) -> None:
        artifact = replace(self.artifact, model=_ZeroRepresentationModel())
        batch = materialize_candidate_batch(
            artifact,
            [0],
            config=CandidateMaterializationConfig(
                top_k=3,
                user_block_size=1,
                item_block_size=2,
                checkpoint_user_count=1,
            ),
        )
        self.assertEqual(batch.movie_ids.tolist(), [10, 20, 30])

    def test_dynamic_worker_queue_matches_sequential_result(self) -> None:
        base_config = CandidateMaterializationConfig(
            top_k=3,
            user_block_size=1,
            item_block_size=2,
            checkpoint_user_count=3,
            worker_count=1,
        )
        exclusions = {101: {10}, 202: {30}}
        sequential = materialize_candidate_batch(
            self.artifact,
            [0, 1, 2],
            exclusions_by_user_id=exclusions,
            config=base_config,
        )
        parallel = materialize_candidate_batch(
            self.artifact,
            [0, 1, 2],
            exclusions_by_user_id=exclusions,
            config=replace(base_config, worker_count=2),
        )

        self.assertEqual(parallel.failures, sequential.failures)
        np.testing.assert_array_equal(parallel.successful_user_ids, sequential.successful_user_ids)
        np.testing.assert_array_equal(parallel.candidate_user_ids, sequential.candidate_user_ids)
        np.testing.assert_array_equal(parallel.movie_ids, sequential.movie_ids)
        np.testing.assert_array_equal(parallel.source_ranks, sequential.source_ranks)
        np.testing.assert_allclose(parallel.model_scores, sequential.model_scores, atol=1e-7)
        self.assertGreaterEqual(
            parallel.peak_score_block_bytes,
            sequential.peak_score_block_bytes,
        )

    def test_worker_count_does_not_change_result_config_hash(self) -> None:
        sequential = CandidateMaterializationConfig(worker_count=1)
        parallel = replace(sequential, worker_count=4)

        self.assertEqual(parallel.result_config, sequential.result_config)
        self.assertEqual(parallel.config_hash, sequential.config_hash)
        self.assertNotEqual(parallel.execution_config, sequential.execution_config)

    def test_known_user_score_centering_matches_manual_scores(self) -> None:
        weight = 0.9
        artifact = replace(
            self.artifact,
            config=replace(
                self.artifact.config,
                known_user_score_centering_weight=weight,
            ),
        )
        batch = materialize_candidate_batch(
            artifact,
            [0],
            config=CandidateMaterializationConfig(
                top_k=3,
                user_block_size=1,
                item_block_size=2,
                checkpoint_user_count=1,
            ),
        )
        all_user_biases, all_user_embeddings = artifact.model.get_user_representations(
            artifact.user_features
        )
        item_biases, item_embeddings = artifact.model.get_item_representations(
            artifact.item_features
        )
        user_embedding = (
            all_user_embeddings[0] - weight * np.mean(all_user_embeddings, axis=0)
        )
        scores = item_embeddings @ user_embedding
        scores += (1.0 - weight) * item_biases
        scores += all_user_biases[0] - weight * np.mean(all_user_biases)
        expected = sorted(
            range(len(artifact.movie_ids)),
            key=lambda index: (-float(scores[index]), int(artifact.movie_ids[index])),
        )[:3]
        self.assertEqual(
            batch.movie_ids.tolist(),
            [int(artifact.movie_ids[index]) for index in expected],
        )
        np.testing.assert_allclose(batch.model_scores, scores[expected], atol=1e-6)

    def test_failed_user_is_isolated_after_block_retry(self) -> None:
        artifact = replace(
            self.artifact,
            model=_OneUserFailureModel(self.artifact.model, failing_identity_column=1),
        )
        batch = materialize_candidate_batch(
            artifact,
            [0, 1, 2],
            config=CandidateMaterializationConfig(
                top_k=2,
                user_block_size=3,
                item_block_size=3,
                checkpoint_user_count=3,
                worker_count=2,
            ),
        )
        self.assertEqual(batch.successful_user_ids.tolist(), [101, 303])
        self.assertEqual([failure.user_id for failure in batch.failures], [202])
        self.assertEqual(set(batch.candidate_user_ids.tolist()), {101, 303})

    def test_snapshot_is_immutable_reloadable_and_publishable_without_commit(self) -> None:
        config = CandidateMaterializationConfig(
            top_k=2,
            user_block_size=1,
            item_block_size=2,
            checkpoint_user_count=2,
        )
        with tempfile.TemporaryDirectory(prefix="v3-candidate-snapshot-") as temporary:
            snapshot = materialize_candidate_snapshot(
                self.artifact,
                exclusions_by_user_id={101: {10}},
                config=config,
                output_root=temporary,
            )
            same_snapshot = materialize_candidate_snapshot(
                self.artifact,
                exclusions_by_user_id={101: {10}},
                config=config,
                output_root=temporary,
            )
            loaded = load_candidate_snapshot(snapshot.path)
            batches = list(iter_candidate_snapshot_batches(loaded))

            self.assertEqual(same_snapshot.path, snapshot.path)
            self.assertEqual(loaded.manifest["successful_user_count"], 3)
            self.assertEqual(loaded.manifest["candidate_count"], 6)
            self.assertEqual(sum(batch.candidate_count for batch in batches), 6)
            self.assertGreaterEqual(loaded.manifest["seconds_per_successful_user"], 0.0)

            session = _RecordingSession()
            diagnostics = publish_candidate_snapshot(session, loaded, statement_chunk_size=3)
            inserted_rows = [
                row
                for _statement, parameters in session.calls
                if parameters
                for row in parameters
            ]
            self.assertTrue(session.flushed)
            self.assertEqual(diagnostics["replaced_user_count"], 3)
            self.assertEqual(diagnostics["inserted_candidate_count"], 6)
            self.assertEqual(len(inserted_rows), 6)
            self.assertTrue(all(row["source"] == "lightfm_v3" for row in inserted_rows))
            self.assertTrue(
                all(row["source_scores"]["candidate_snapshot_id"] == loaded.snapshot_id for row in inserted_rows)
            )


if __name__ == "__main__":
    unittest.main()
