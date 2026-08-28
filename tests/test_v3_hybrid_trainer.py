from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

import numpy as np
from scipy.sparse import csr_matrix, vstack

from app.jobs.recsys.v3.training.artifact_publisher import (
    load_hybrid_artifact,
    publish_hybrid_artifact,
)
from app.jobs.recsys.v3.features.feature_representation import (
    transform_item_feature_export,
    transform_user_feature_export,
)
from app.jobs.recsys.v3.features.feature_schemas import ItemFeatureExport, ItemFeatureManifest
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import (
    ModelHealthError,
    apply_item_frequency_weighting,
    assert_model_health,
    evaluate_model_health,
    train_hybrid_model,
)
from app.jobs.recsys.v3.features.user_feature_builder import (
    build_user_feature_export,
    hash_ordered_values,
)
from tests.test_v3_identity_trainer import synthetic_dataset


def synthetic_item_export() -> ItemFeatureExport:
    movie_ids = (10, 20, 30, 40, 50)
    tokens = (
        "movie:10",
        "movie:20",
        "movie:30",
        "movie:40",
        "movie:50",
        "genre:1",
        "genre:2",
        "theme:healing",
    )
    matrix = csr_matrix(
        np.array(
            [
                [1, 0, 0, 0, 0, 1, 0, 0.8],
                [0, 1, 0, 0, 0, 1, 0, 0.4],
                [0, 0, 1, 0, 0, 0, 1, 0.0],
                [0, 0, 0, 1, 0, 0, 1, 0.7],
                [0, 0, 0, 0, 1, 1, 0, 0.0],
            ],
            dtype=np.float32,
        )
    )
    manifest = ItemFeatureManifest(
        exporter_version="test-v1",
        ontology_build_id=22,
        ontology_engine_name="v3",
        ontology_schema_version="v3.0",
        ontology_source_hash="s" * 64,
        movie_count=len(movie_ids),
        feature_count=len(tokens),
        matrix_nnz=int(matrix.nnz),
        matrix_shape=matrix.shape,
        movie_mapping_hash=hash_ordered_values("movie", (str(item) for item in movie_ids)),
        feature_mapping_hash=hash_ordered_values("feature", tokens),
        export_hash="e" * 64,
        pruning_rules={},
        family_diagnostics=(),
        ontology_build_status="success",
    )
    return ItemFeatureExport(
        movie_ids=movie_ids,
        movie_id_map={value: index for index, value in enumerate(movie_ids)},
        feature_tokens=tokens,
        feature_token_map={value: index for index, value in enumerate(tokens)},
        item_features=matrix,
        manifest=manifest,
    )


def synthetic_user_export(item_export: ItemFeatureExport):
    return build_user_feature_export(
        user_ids=(101, 202, 303),
        explicit_genre_rows=((101, 1), (202, 2)),
        favorite_rows=((101, 10), (303, 40), (303, 999)),
        item_export=item_export,
    )


class UserFeatureBuilderTest(unittest.TestCase):
    def test_user_features_keep_identity_and_bounded_onboarding_vocabulary(self) -> None:
        item_export = synthetic_item_export()
        export = synthetic_user_export(item_export)

        self.assertEqual(export.feature_tokens[:3], ("user:101", "user:202", "user:303"))
        self.assertIn("genre:1", export.feature_token_map)
        self.assertIn("genre:2", export.feature_token_map)
        self.assertIn("theme:healing", export.feature_token_map)
        self.assertNotIn("movie:10", export.feature_token_map)
        self.assertEqual(export.manifest.missing_favorite_movie_count, 1)
        self.assertEqual(export.manifest.covered_user_count, 3)
        self.assertEqual(export.manifest.item_feature_export_hash, "e" * 64)
        self.assertEqual(export.user_features.shape[0], 3)
        self.assertTrue(np.all(export.user_features.data > 0))

    def test_feature_representation_normalizes_semantics_and_limits_item_identity(self) -> None:
        original = synthetic_item_export()
        transformed = transform_item_feature_export(
            original,
            policy="supported_identity_normalized",
            supported_movie_ids=frozenset({10, 40}),
        )

        movie_count = len(original.movie_ids)
        identities = transformed.item_features[:, :movie_count]
        semantics = transformed.item_features[:, movie_count:]
        self.assertEqual(identities.nnz, 2)
        self.assertEqual(float(identities[0, 0]), 1.0)
        self.assertEqual(float(identities[3, 3]), 1.0)
        semantic_sums = np.asarray(semantics.sum(axis=1)).reshape(-1)
        np.testing.assert_allclose(semantic_sums, np.ones(5), atol=1e-6)
        self.assertEqual(
            transformed.manifest.representation_policy,
            "supported_identity_normalized",
        )
        self.assertNotEqual(transformed.manifest.export_hash, original.manifest.export_hash)

        user_export = synthetic_user_export(transformed)
        normalized_users = transform_user_feature_export(
            user_export,
            policy="supported_identity_normalized",
        )
        row_sums = np.asarray(normalized_users.user_features.sum(axis=1)).reshape(-1)
        np.testing.assert_allclose(row_sums, np.full(3, 2.0), atol=1e-6)

    def test_metadata_only_representation_removes_all_item_identity_values(self) -> None:
        transformed = transform_item_feature_export(
            synthetic_item_export(),
            policy="metadata_only_normalized",
        )
        movie_count = len(transformed.movie_ids)
        self.assertEqual(transformed.item_features[:, :movie_count].nnz, 0)


class HybridTrainerTest(unittest.TestCase):
    def test_train_publish_reload_preserves_hybrid_predictions(self) -> None:
        item_export = synthetic_item_export()
        user_export = synthetic_user_export(item_export)
        config = LightFMTrainingConfig(
            stage="hybrid_ontology",
            no_components=4,
            epochs=3,
            num_threads=1,
        )
        result = train_hybrid_model(
            synthetic_dataset(),
            item_export=item_export,
            user_export=user_export,
            config=config,
        )

        with tempfile.TemporaryDirectory(prefix="v3-hybrid-test-") as temporary:
            artifact_path = publish_hybrid_artifact(result, temporary)
            loaded = load_hybrid_artifact(artifact_path)

            self.assertEqual(loaded.manifest["ontology"]["build_id"], 22)
            self.assertTrue(loaded.manifest["ontology"]["applicable"])
            self.assertEqual(loaded.user_features.shape, user_export.user_features.shape)
            self.assertEqual(loaded.item_features.shape, item_export.item_features.shape)
            self.assertEqual(loaded.manifest["feature_exports"]["item_export_hash"], "e" * 64)

    def test_feature_only_new_user_and_item_receive_finite_scores(self) -> None:
        item_export = synthetic_item_export()
        user_export = synthetic_user_export(item_export)
        result = train_hybrid_model(
            synthetic_dataset(),
            item_export=item_export,
            user_export=user_export,
            config=LightFMTrainingConfig(
                stage="hybrid_ontology",
                no_components=4,
                epochs=3,
            ),
        )
        new_item = csr_matrix(
            ([1.0], ([0], [item_export.feature_token_map["genre:1"]])),
            shape=(1, item_export.item_features.shape[1]),
            dtype=np.float32,
        )
        extended_items = vstack([item_export.item_features, new_item], format="csr")
        new_user = csr_matrix(
            ([1.0], ([0], [user_export.feature_token_map["genre:1"]])),
            shape=(1, user_export.user_features.shape[1]),
            dtype=np.float32,
        )
        extended_users = vstack([user_export.user_features, new_user], format="csr")

        new_item_score = result.model.predict(
            0,
            np.array([5], dtype=np.int32),
            user_features=user_export.user_features,
            item_features=extended_items,
            num_threads=1,
        )
        new_user_score = result.model.predict(
            3,
            np.array([0], dtype=np.int32),
            user_features=extended_users,
            item_features=item_export.item_features,
            num_threads=1,
        )
        self.assertTrue(np.isfinite(new_item_score).all())
        self.assertTrue(np.isfinite(new_user_score).all())

    def test_hybrid_training_rejects_movie_mapping_mismatch(self) -> None:
        item_export = synthetic_item_export()
        user_export = synthetic_user_export(item_export)
        incompatible = replace(
            item_export,
            movie_ids=(10, 20, 30, 40, 60),
            movie_id_map={10: 0, 20: 1, 30: 2, 40: 3, 60: 4},
        )
        with self.assertRaisesRegex(ValueError, "movie mappings must match"):
            train_hybrid_model(
                synthetic_dataset(),
                item_export=incompatible,
                user_export=user_export,
                config=LightFMTrainingConfig(stage="hybrid_ontology", epochs=1),
            )

    def test_hybrid_training_rejects_non_successful_ontology_features(self) -> None:
        item_export = synthetic_item_export()
        user_export = synthetic_user_export(item_export)
        running = replace(
            item_export,
            manifest=replace(item_export.manifest, ontology_build_status="running"),
        )
        with self.assertRaisesRegex(ValueError, "successful ontology build"):
            train_hybrid_model(
                synthetic_dataset(),
                item_export=running,
                user_export=user_export,
                config=LightFMTrainingConfig(stage="hybrid_ontology", epochs=1),
            )

    def test_model_health_rejects_finite_but_exploded_parameters(self) -> None:
        class ExplodedModel:
            user_embeddings = np.full((3, 4), 1_000_000.0, dtype=np.float32)
            item_embeddings = np.full((5, 4), 1.0, dtype=np.float32)
            user_biases = np.zeros(3, dtype=np.float32)
            item_biases = np.zeros(5, dtype=np.float32)

            @staticmethod
            def predict(user_ids, item_ids, **_kwargs):
                return np.full(np.asarray(item_ids).shape, 4_000_000.0, dtype=np.float32)

            @classmethod
            def get_user_representations(cls, _features=None):
                return cls.user_biases, cls.user_embeddings

            @classmethod
            def get_item_representations(cls, _features=None):
                return cls.item_biases, cls.item_embeddings

        report = evaluate_model_health(
            ExplodedModel(),
            user_count=3,
            movie_count=5,
            num_threads=1,
        )

        self.assertEqual(report["status"], "fail")
        self.assertIn("user_embeddings.max_abs", report["violations"])
        self.assertIn("prediction.max_abs", report["violations"])
        with self.assertRaises(ModelHealthError):
            assert_model_health(report)

    def test_inverse_sqrt_frequency_weighting_reduces_common_item_weight(self) -> None:
        dataset = synthetic_dataset()
        interactions, weights = (
            dataset.interactions.tocoo(),
            dataset.sample_weights.tocoo(),
        )
        adjusted, diagnostics = apply_item_frequency_weighting(
            interactions,
            weights,
            mode="inverse_sqrt",
        )
        by_coordinate = {
            (int(row), int(column)): float(value)
            for row, column, value in zip(
                adjusted.row, adjusted.col, adjusted.data, strict=True
            )
        }
        self.assertEqual(diagnostics["mode"], "inverse_sqrt")
        self.assertLess(by_coordinate[(0, 1)] / 2.0, by_coordinate[(0, 0)] / 1.0)


if __name__ == "__main__":
    unittest.main()
