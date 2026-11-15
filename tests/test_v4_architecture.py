from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix

from app.schemas.recsys import RecommendationMode
from app.jobs.recsys.v4.candidates.candidate_materializer import (
    materialize_candidate_batch,
)
from app.jobs.recsys.v4.candidates.candidate_schemas import (
    CandidateMaterializationConfig,
)
from app.jobs.recsys.v4.candidates.candidate_snapshot import (
    load_candidate_snapshot,
)
from app.jobs.recsys.v4.datasets.dataset_schemas import (
    DatasetDiagnostics,
    LightFMDatasetSnapshot,
)
from app.jobs.recsys.v4.features.feature_schemas import (
    ItemFeatureExport,
    ItemFeatureManifest,
)
from app.jobs.recsys.v4.features.user_feature_builder import (
    build_user_feature_export,
    hash_ordered_values,
)
from app.jobs.recsys.v4.training.artifact_publisher import (
    load_hybrid_artifact,
    publish_hybrid_artifact,
)
from app.jobs.recsys.v4.training.model_schemas import (
    SUPPORTED_LIGHTFM_LOSSES,
    LightFMTrainingConfig,
)
from app.jobs.recsys.v4.training.trainer import train_hybrid_model
from app.models.recommendations import Recommendation
from app.services.recsys.registry import get_recommendation_adapter
from app.services.recsys.v4.config import SERVING_BUNDLE_ROOT
from app.services.recsys.v4.domain.ontology_registry import (
    ONTOLOGY_ENGINE_NAME,
    ONTOLOGY_SCHEMA_VERSION,
)
from app.services.recsys.v4.retrieval.eligibility_schemas import (
    CandidateEligibilityDiagnostics,
)
from app.services.recsys.v4.retrieval.retrieval_schemas import (
    CandidateMergeDiagnostics,
    CandidateMergeResult,
    CandidateSource,
    LongTermCandidate,
    MergedCandidate,
    ShortTermCandidate,
)
from app.services.recsys.v4.retrieval.candidate_merger import (
    merge_lightfm_and_short_term_candidates,
)
from app.services.recsys.v4.retrieval.retrieval_pipeline import (
    build_retrieval_candidates,
)
from app.services.recsys.v4.recommender import get_recommendations
from app.services.recsys.v4.profile_change import (
    V4_SHORT_TERM_PROCESSING_USERS_KEY,
    V4_SHORT_TERM_SCHEDULED_USERS_KEY,
    short_term_revision_key,
)
from app.services.recsys.v4.serving.artifact_contract import (
    validate_v4_hybrid_artifact,
)


class V4EngineBoundaryTest(unittest.TestCase):
    def test_registry_loads_v4_without_replacing_previous_engines(self) -> None:
        self.assertEqual(get_recommendation_adapter("v1").name, "v1")
        self.assertEqual(get_recommendation_adapter("v2").name, "v2")
        self.assertEqual(get_recommendation_adapter("v3").name, "v3")
        self.assertEqual(get_recommendation_adapter("v4").name, "v4")

    def test_merger_has_no_direct_long_term_ontology_lane(self) -> None:
        result = merge_lightfm_and_short_term_candidates(
            (
                LongTermCandidate(movie_id=10, model_raw_score=3.0, source_rank=1),
                LongTermCandidate(movie_id=20, model_raw_score=2.0, source_rank=2),
            ),
            (
                ShortTermCandidate(
                    movie_id=20,
                    short_term_raw_score=2.0,
                    source_rank=1,
                ),
                ShortTermCandidate(
                    movie_id=30,
                    short_term_raw_score=1.0,
                    source_rank=2,
                ),
            ),
            drift_confidence=0.8,
            limit=3,
        )

        self.assertEqual({item.movie_id for item in result.candidates}, {10, 20, 30})
        allowed_sources = {CandidateSource.MODEL, CandidateSource.SHORT_TERM_CONTEXT}
        self.assertTrue(all(set(item.sources) <= allowed_sources for item in result.candidates))

    def test_known_user_pipeline_never_calls_long_term_ontology_retrieval(self) -> None:
        long_term = (
            LongTermCandidate(movie_id=10, model_raw_score=3.0, source_rank=1),
        )
        merged_candidate = MergedCandidate(
            movie_id=10,
            sources=(CandidateSource.MODEL,),
            selection_rank=1,
            candidate_selection_score=1.0,
            model_raw_score=3.0,
            normalized_long_term_score=1.0,
            model_source_rank=1,
        )
        merge_result = CandidateMergeResult(
            candidates=(merged_candidate,),
            diagnostics=CandidateMergeDiagnostics(
                long_term_source_count=1,
                short_term_source_count=0,
                raw_union_count=1,
                selected_count=1,
                drift_confidence=0.0,
                drift_weight=0.0,
                contextual_floor_count=0,
            ),
        )
        profile = SimpleNamespace(
            long_term=SimpleNamespace(positive_pair_count=10),
            short_term=SimpleNamespace(drift_confidence=0.0),
        )
        short_term = SimpleNamespace(candidates=())
        eligibility = SimpleNamespace(
            candidates=(merged_candidate,),
            diagnostics=CandidateEligibilityDiagnostics(
                input_candidate_count=1,
                inspected_candidate_count=1,
                selected_candidate_count=1,
            ),
            rejections=(),
        )
        ontology = SimpleNamespace(
            candidates=(SimpleNamespace(movie_id=10),),
        )

        with (
            patch(
                "app.services.recsys.v4.retrieval.retrieval_pipeline."
                "retrieve_cached_short_term_candidates",
                return_value=short_term,
            ),
            patch(
                "app.services.recsys.v4.retrieval.retrieval_pipeline."
                "merge_lightfm_and_short_term_candidates",
                return_value=merge_result,
            ),
            patch(
                "app.services.recsys.v4.retrieval.retrieval_pipeline."
                "select_eligible_candidates",
                return_value=eligibility,
            ),
            patch(
                "app.services.recsys.v4.retrieval.retrieval_pipeline."
                "analyze_candidates",
                return_value=ontology,
            ) as analyze,
        ):
            result = build_retrieval_candidates(
                object(),
                ontology_build_id=22,
                profile=profile,
                long_term_candidates=long_term,
                context=SimpleNamespace(),
                artifact=SimpleNamespace(movie_identity_supported=lambda _movie_id: True),
            )

        analyze.assert_called_once()
        self.assertEqual(result.merged.candidates[0].movie_id, 10)

    def test_v4_packages_do_not_import_v3_packages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        package_roots = (
            root / "app" / "services" / "recsys" / "v4",
            root / "app" / "jobs" / "recsys" / "v4",
        )
        violations: list[str] = []
        for package_root in package_roots:
            for path in package_root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    module = None
                    if isinstance(node, ast.ImportFrom):
                        module = node.module
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if ".recsys.v3" in alias.name:
                                violations.append(f"{path.relative_to(root)}:{node.lineno}")
                    if module and ".recsys.v3" in module:
                        violations.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_recommender_uses_v4_pipeline_and_preserves_pagination(self) -> None:
        model = SimpleNamespace(
            user_index=lambda _user_id: 0,
            collaborative_population_confidence=0.5,
        )
        bundle = SimpleNamespace(
            model=model,
            ontology_build_id=22,
            bundle_id="bundle-test",
        )
        profile = SimpleNamespace(
            long_term=SimpleNamespace(
                excluded_movie_ids=frozenset(),
                positive_pair_count=10,
            ),
            short_term=SimpleNamespace(
                recent_negative_movie_ids=frozenset(),
            ),
        )
        published = (
            LongTermCandidate(movie_id=10, model_raw_score=3.0, source_rank=1),
        )
        ranked = tuple(
            SimpleNamespace(
                movie_id=movie_id,
                candidate=SimpleNamespace(sources=(CandidateSource.MODEL,)),
            )
            for movie_id in (10, 20, 30, 40)
        )
        retrieval = SimpleNamespace(
            short_term=SimpleNamespace(
                candidates=(),
                diagnostics=SimpleNamespace(
                    cache_status="hit",
                    profile_signature="profile",
                ),
            ),
            merged=CandidateMergeResult(
                candidates=(),
                diagnostics=CandidateMergeDiagnostics(
                    long_term_source_count=1,
                    short_term_source_count=0,
                    raw_union_count=1,
                    selected_count=0,
                    drift_confidence=0.0,
                    drift_weight=0.0,
                    contextual_floor_count=0,
                ),
            ),
            eligibility=CandidateEligibilityDiagnostics(),
        )
        with (
            patch(
                "app.services.recsys.v4.recommender.get_redis",
                return_value=object(),
            ),
            patch(
                "app.services.recsys.v4.recommender.get_active_v4_serving_bundle",
                return_value=bundle,
            ),
            patch(
                "app.services.recsys.v4.recommender.build_user_runtime_profile",
                return_value=SimpleNamespace(bundle=profile),
            ),
            patch(
                "app.services.recsys.v4.recommender."
                "load_published_candidates",
                return_value=(published, "snapshot"),
            ),
            patch(
                "app.services.recsys.v4.recommender.onboarding_features_changed",
                return_value=False,
            ),
            patch(
                "app.services.recsys.v4.recommender.build_retrieval_candidates",
                return_value=retrieval,
            ) as build_retrieval,
            patch(
                "app.services.recsys.v4.recommender.evaluate_policy_candidates",
                return_value=SimpleNamespace(candidates=ranked),
            ),
            patch("app.services.recsys.v4.recommender._policy_context"),
            patch("app.services.recsys.v4.recommender.persist_request_diagnostics"),
        ):
            response = get_recommendations(
                object(),
                user_id=101,
                mode=RecommendationMode.ALL,
                limit=2,
                offset=1,
            )

        build_retrieval.assert_called_once()
        self.assertEqual(response.movie_ids, [20, 30])
        self.assertEqual(response.count, 2)
        self.assertTrue(response.has_more)
        self.assertEqual(response.source, "v4_hybrid_lightfm")


class V4HybridArtifactContractTest(unittest.TestCase):
    def test_accepts_populated_user_and_item_ontology_feature_blocks(self) -> None:
        validate_v4_hybrid_artifact(_artifact())

    def test_rejects_identity_only_feature_dimensions(self) -> None:
        artifact = _artifact()
        artifact.user_features = csr_matrix(np.asarray([[1.0]], dtype=np.float32))
        artifact.user_feature_tokens = ("user_identity:1",)

        with self.assertRaisesRegex(ValueError, "user ontology feature columns"):
            validate_v4_hybrid_artifact(artifact)


class V4OfflineOwnershipTest(unittest.TestCase):
    def test_v4_owns_artifact_and_ontology_namespaces(self) -> None:
        self.assertEqual(SERVING_BUNDLE_ROOT, "assets/ml_models/v4")
        self.assertEqual(ONTOLOGY_ENGINE_NAME, "v4")
        self.assertEqual(ONTOLOGY_SCHEMA_VERSION, "v4.0")

    def test_all_lightfm_losses_are_selectable(self) -> None:
        self.assertEqual(
            SUPPORTED_LIGHTFM_LOSSES,
            ("logistic", "bpr", "warp", "warp-kos"),
        )
        for loss in SUPPORTED_LIGHTFM_LOSSES:
            self.assertEqual(LightFMTrainingConfig(loss=loss).loss, loss)
        with self.assertRaisesRegex(ValueError, "unsupported V4 LightFM loss"):
            LightFMTrainingConfig(loss="invalid")

    def test_precomputed_candidate_identity_is_scoped_by_engine(self) -> None:
        primary_key_columns = tuple(
            column.name for column in Recommendation.__table__.primary_key.columns
        )
        self.assertEqual(primary_key_columns, ("user_id", "movie_id", "engine"))

    def test_short_term_redis_state_is_scoped_to_v4(self) -> None:
        self.assertEqual(
            V4_SHORT_TERM_SCHEDULED_USERS_KEY,
            "recsys:v4:short_term:scheduled_users:v2",
        )
        self.assertEqual(
            V4_SHORT_TERM_PROCESSING_USERS_KEY,
            "recsys:v4:short_term:processing_users:v2",
        )
        self.assertEqual(short_term_revision_key(17), "user:17:v4:short_term:revision")

    def test_rejects_v3_owned_artifact(self) -> None:
        artifact = _artifact()
        artifact.manifest["engine_name"] = "v3"

        with self.assertRaisesRegex(ValueError, "owned by the V4 engine"):
            validate_v4_hybrid_artifact(artifact)

    def test_rejects_disabled_semantic_block(self) -> None:
        artifact = _artifact()
        artifact.manifest["feature_exports"]["item_semantic_block_weight"] = 0.0

        with self.assertRaisesRegex(ValueError, "item_semantic_block_weight"):
            validate_v4_hybrid_artifact(artifact)

    def test_rejects_disconnected_user_and_item_semantic_spaces(self) -> None:
        artifact = _artifact()
        artifact.user_feature_tokens = ("user_identity:1", "genre:9")

        with self.assertRaisesRegex(ValueError, "share a semantic space"):
            validate_v4_hybrid_artifact(artifact)

    def test_rejects_v3_candidate_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v4-snapshot-owner-") as temporary:
            path = Path(temporary)
            (path / "manifest.json").write_text(
                json.dumps(
                    {
                        "snapshot_format_version": 1,
                        "engine_name": "v3",
                        "engine_version": "v3.0.0",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "owned by the V4 engine"):
                load_candidate_snapshot(path)

    def test_trains_reloads_and_scores_a_v4_hybrid_artifact(self) -> None:
        dataset = _training_dataset()
        item_export = _training_item_export()
        user_export = build_user_feature_export(
            user_ids=dataset.user_ids,
            explicit_genre_rows=((101, 1), (202, 2), (303, 2)),
            favorite_rows=((101, 10), (202, 30), (303, 40)),
            item_export=item_export,
        )
        result = train_hybrid_model(
            dataset,
            item_export=item_export,
            user_export=user_export,
            config=LightFMTrainingConfig(
                stage="hybrid_ontology",
                no_components=4,
                epochs=3,
                num_threads=1,
            ),
        )

        with tempfile.TemporaryDirectory(prefix="v4-hybrid-") as temporary:
            artifact_path = publish_hybrid_artifact(result, temporary)
            loaded = load_hybrid_artifact(artifact_path)
            batch = materialize_candidate_batch(
                loaded,
                (0, 1, 2),
                initial_eligible_item_mask=np.ones(5, dtype=np.bool_),
                config=CandidateMaterializationConfig(
                    top_k=2,
                    user_block_size=2,
                    item_block_size=3,
                    checkpoint_user_count=2,
                ),
            )

        self.assertEqual(loaded.manifest["engine_name"], "v4")
        self.assertEqual(loaded.manifest["ontology"]["engine_name"], "v4")
        self.assertEqual(batch.successful_user_ids.tolist(), [101, 202, 303])
        self.assertEqual(batch.candidate_count, 6)

    def test_training_rejects_a_v3_ontology_export(self) -> None:
        dataset = _training_dataset()
        item_export = _training_item_export()
        item_export = replace(
            item_export,
            manifest=replace(item_export.manifest, ontology_engine_name="v3"),
        )
        user_export = build_user_feature_export(
            user_ids=dataset.user_ids,
            explicit_genre_rows=((101, 1),),
            favorite_rows=(),
            item_export=item_export,
        )

        with self.assertRaisesRegex(ValueError, "requires a V4 ontology"):
            train_hybrid_model(
                dataset,
                item_export=item_export,
                user_export=user_export,
                config=LightFMTrainingConfig(
                    stage="hybrid_ontology",
                    no_components=4,
                    epochs=1,
                ),
            )


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(
        manifest={
            "engine_name": "v4",
            "stage": "hybrid_ontology",
            "feature_exports": {
                "item_semantic_block_weight": 1.0,
                "user_semantic_block_weight": 0.5,
            },
        },
        user_ids=np.asarray([1], dtype=np.int64),
        movie_ids=np.asarray([10], dtype=np.int64),
        user_features=csr_matrix(np.asarray([[1.0, 0.5]], dtype=np.float32)),
        item_features=csr_matrix(np.asarray([[1.0, 0.5]], dtype=np.float32)),
        user_feature_tokens=("user_identity:1", "genre:1"),
        item_feature_tokens=("movie_identity:10", "genre:1"),
    )


def _training_dataset() -> LightFMDatasetSnapshot:
    user_ids = (101, 202, 303)
    movie_ids = (10, 20, 30, 40, 50)
    rows = np.asarray([0, 0, 1, 1, 2, 2], dtype=np.int32)
    columns = np.asarray([0, 1, 1, 2, 2, 3], dtype=np.int32)
    interactions = np.ones(rows.size, dtype=np.float32)
    weights = np.asarray([1.0, 2.0, 1.5, 2.0, 1.0, 2.3], dtype=np.float32)
    cutoff = datetime(2026, 9, 15, 0, 0, 0)
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
        dataset_hash="d" * 64,
    )
    shape = (len(user_ids), len(movie_ids))
    return LightFMDatasetSnapshot(
        data_cutoff_at=cutoff,
        user_ids=user_ids,
        movie_ids=movie_ids,
        user_id_map={value: index for index, value in enumerate(user_ids)},
        movie_id_map={value: index for index, value in enumerate(movie_ids)},
        interactions=coo_matrix((interactions, (rows, columns)), shape=shape),
        sample_weights=coo_matrix((weights, (rows, columns)), shape=shape),
        positives=(),
        diagnostics=diagnostics,
    )


def _training_item_export() -> ItemFeatureExport:
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
        np.asarray(
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
        ontology_build_id=24,
        ontology_engine_name="v4",
        ontology_schema_version="v4.0",
        ontology_source_hash="s" * 64,
        movie_count=len(movie_ids),
        feature_count=len(tokens),
        matrix_nnz=int(matrix.nnz),
        matrix_shape=matrix.shape,
        movie_mapping_hash=hash_ordered_values(
            "movie", (str(value) for value in movie_ids)
        ),
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


if __name__ == "__main__":
    unittest.main()
