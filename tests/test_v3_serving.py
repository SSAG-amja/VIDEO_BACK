from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.recsys.v3.training.artifact_publisher import publish_hybrid_artifact
from app.jobs.recsys.v3.features.feature_representation import (
    transform_item_feature_export,
    transform_user_feature_export,
)
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig
from app.jobs.recsys.v3.candidates.candidate_snapshot import materialize_candidate_snapshot
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.serving.serving_bundle_publisher import activate_serving_bundle
from app.jobs.recsys.v3.training.trainer import train_hybrid_model
from app.jobs.recsys.v3.diagnostics.online_baseline import validate_response
from app.schemas.recsys import RecommendationMode
from app.services.recsys.v3.errors import V3NotReadyError
from app.services.recsys.v3.retrieval.eligibility_schemas import CandidateEligibilityDiagnostics
from app.services.recsys.v3.retrieval.lightfm_retriever import (
    build_feature_only_user_row,
    onboarding_features_changed,
    retrieve_lightfm_candidates,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from app.services.recsys.v3.recommender import _policy_context, get_recommendations
from app.services.recsys.v3.retrieval.retrieval_schemas import CandidateSource, LongTermCandidate
from app.services.recsys.v3.serving.serving_bundle import ServingBundleCache
from tests.test_v3_hybrid_trainer import synthetic_item_export, synthetic_user_export
from tests.test_v3_identity_trainer import synthetic_dataset
from tests.test_v3_retrieval import retrieval_profile


class _ActivationSession:
    def __init__(self, build):
        self.build = build
        self.committed = False

    def get(self, _model, build_id):
        return self.build if build_id == self.build.id else None

    def execute(self, _statement):
        return None

    def flush(self):
        return None

    def commit(self):
        self.committed = True


def _profile_for_user(user_id: int):
    profile = retrieval_profile()
    return replace(
        profile,
        user_id=user_id,
        onboarding=replace(profile.onboarding, user_id=user_id),
        long_term=replace(profile.long_term, user_id=user_id),
        short_term=replace(profile.short_term, user_id=user_id),
        serving_context=replace(profile.serving_context, user_id=user_id),
    )


class ServingBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="v3-serving-")
        cls.root = Path(cls.temporary.name)
        item_export = synthetic_item_export()
        result = train_hybrid_model(
            synthetic_dataset(),
            item_export=item_export,
            user_export=synthetic_user_export(item_export),
            config=LightFMTrainingConfig(
                stage="hybrid_ontology",
                no_components=4,
                epochs=3,
                num_threads=1,
            ),
        )
        cls.model_path = publish_hybrid_artifact(result, cls.root)
        from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact

        artifact = load_hybrid_artifact(cls.model_path)
        cls.snapshot = materialize_candidate_snapshot(
            artifact,
            config=CandidateMaterializationConfig(
                top_k=3,
                user_block_size=2,
                item_block_size=2,
                checkpoint_user_count=2,
            ),
            output_root=cls.root / "candidate_snapshots",
        )
        build = SimpleNamespace(
            id=22,
            status="success",
            engine_name="v3",
            schema_version="v3.0",
            source_hash="s" * 64,
            is_active=False,
        )
        session = _ActivationSession(build)
        cls.manifest = activate_serving_bundle(
            session,
            model_artifact_path=cls.model_path,
            candidate_snapshot_path=cls.snapshot.path,
            artifact_root=cls.root,
            require_candidate_publication=False,
        )
        cls.activation_session = session

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        pointer = {
            "serving_bundle_format_version": 1,
            "bundle_id": self.manifest["bundle_id"],
            "manifest_path": f"serving_bundles/{self.manifest['bundle_id']}/manifest.json",
            "manifest_sha256": _hash_file(
                self.root / "serving_bundles" / self.manifest["bundle_id"] / "manifest.json"
            ),
            "activated_at": "test",
        }
        (self.root / "active_bundle.json").write_text(
            json.dumps(pointer), encoding="utf-8"
        )

    def test_bundle_activation_loads_validated_model_once(self) -> None:
        cache = ServingBundleCache(self.root)
        first = cache.get()
        second = cache.get()
        self.assertIs(first, second)
        self.assertEqual(first.bundle_id, self.manifest["bundle_id"])
        self.assertEqual(first.ontology_build_id, 22)
        self.assertTrue(self.activation_session.committed)

    def test_invalid_reload_keeps_previous_valid_bundle(self) -> None:
        cache = ServingBundleCache(self.root)
        valid = cache.get()
        pointer_path = self.root / "active_bundle.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = "0" * 64
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

        self.assertIs(cache.get(), valid)
        with self.assertRaises(V3NotReadyError):
            ServingBundleCache(self.root).get()

    def test_feature_only_lightfm_scores_without_user_identity(self) -> None:
        bundle = ServingBundleCache(self.root).get()
        profile = retrieval_profile()
        profile = replace(
            profile,
            onboarding=replace(
                profile.onboarding,
                favorite_movie_ids=frozenset({10}),
            ),
        )
        candidates = retrieve_lightfm_candidates(
            bundle.model,
            profile=profile,
            excluded_movie_ids={10},
            force_feature_only=True,
            limit=3,
        )
        self.assertTrue(candidates)
        self.assertNotIn(10, {item.movie_id for item in candidates})
        self.assertEqual(
            [item.source_rank for item in candidates],
            list(range(1, len(candidates) + 1)),
        )

    def test_known_user_onboarding_drift_is_detected(self) -> None:
        bundle = ServingBundleCache(self.root).get()
        profile = _profile_for_user(101)
        matching = replace(
            profile,
            onboarding=replace(
                profile.onboarding,
                favorite_movie_ids=frozenset({10}),
                genre_ids=frozenset({1}),
            ),
        )
        changed = replace(
            matching,
            onboarding=replace(matching.onboarding, genre_ids=frozenset({2})),
        )
        self.assertFalse(onboarding_features_changed(bundle.model, matching))
        self.assertTrue(onboarding_features_changed(bundle.model, changed))

    def test_feature_only_row_uses_artifact_semantic_normalization(self) -> None:
        item_export = transform_item_feature_export(
            synthetic_item_export(),
            policy="supported_identity_normalized",
            supported_movie_ids=frozenset({10, 20, 30, 40}),
        )
        user_export = synthetic_user_export(item_export)
        user_export = transform_user_feature_export(
            user_export,
            policy="supported_identity_normalized",
            identity_weight=4.0,
            semantic_weight=0.25,
        )
        result = train_hybrid_model(
            synthetic_dataset(),
            item_export=item_export,
            user_export=user_export,
            config=LightFMTrainingConfig(
                stage="hybrid_ontology",
                no_components=4,
                epochs=2,
                known_user_score_centering_weight=0.9,
            ),
        )
        with tempfile.TemporaryDirectory(prefix="v3-serving-normalized-") as temporary:
            path = publish_hybrid_artifact(result, temporary)
            runtime = load_runtime_hybrid_artifact(path)
            self.assertEqual(runtime.known_user_score_centering_weight, 0.9)
            self.assertEqual(runtime.mean_user_embedding.shape, (4,))
            profile = _profile_for_user(101)
            profile = replace(
                profile,
                onboarding=replace(
                    profile.onboarding,
                    favorite_movie_ids=frozenset({10}),
                    genre_ids=frozenset({1}),
                ),
            )
            feature_only = build_feature_only_user_row(runtime, profile)
            shared = slice(len(runtime.user_ids), runtime.user_features.shape[1])
            difference = runtime.user_features[0, shared] - feature_only[:, shared]
            self.assertFalse(difference.nnz)
            self.assertAlmostEqual(float(feature_only.sum()), 0.25, places=6)

    def test_recommender_preserves_response_pagination_contract(self) -> None:
        bundle = ServingBundleCache(self.root).get()
        profile = _profile_for_user(101)
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
        policy = SimpleNamespace(candidates=ranked)
        with (
            patch("app.services.recsys.v3.recommender.get_active_serving_bundle", return_value=bundle),
            patch(
                "app.services.recsys.v3.recommender.build_user_runtime_profile",
                return_value=SimpleNamespace(bundle=profile),
            ),
            patch(
                "app.services.recsys.v3.recommender._load_published_candidates",
                return_value=(published, "snapshot"),
            ),
            patch(
                "app.services.recsys.v3.recommender.onboarding_features_changed",
                return_value=False,
            ),
            patch(
                "app.services.recsys.v3.recommender.build_retrieval_candidates",
                return_value=SimpleNamespace(
                    short_term=SimpleNamespace(
                        candidates=(),
                        diagnostics=SimpleNamespace(
                            cache_status="hit",
                            profile_signature="test-profile",
                        ),
                    ),
                    eligibility=CandidateEligibilityDiagnostics(),
                ),
            ),
            patch(
                "app.services.recsys.v3.recommender.evaluate_policy_candidates",
                return_value=policy,
            ),
            patch("app.services.recsys.v3.recommender._policy_context"),
            patch("app.services.recsys.v3.recommender._persist_request_diagnostics"),
        ):
            response = get_recommendations(
                object(),
                user_id=101,
                mode=RecommendationMode.ALL,
                limit=2,
                offset=1,
                shuffle_seed="stable",
            )
        self.assertEqual(response.movie_ids, [20, 30])
        self.assertEqual(response.count, 2)
        self.assertTrue(response.has_more)
        self.assertEqual(response.source, "v3_model")

    def test_policy_context_ranks_full_pool_before_slicing_pages(self) -> None:
        profile = _profile_for_user(101)
        with patch(
            "app.services.recsys.v3.recommender.get_blacklisted_movie_ids",
            return_value=set(),
        ):
            context = _policy_context(object(), 101, profile)

        self.assertEqual(context.limit, 100)

    def test_onboarding_change_persists_feature_only_candidates_on_next_request(self) -> None:
        bundle = ServingBundleCache(self.root).get()
        profile = _profile_for_user(101)
        feature_only = (
            LongTermCandidate(movie_id=20, model_raw_score=2.0, source_rank=1),
        )
        cold_start = SimpleNamespace(
            merged=SimpleNamespace(candidates=()),
            ontology=SimpleNamespace(candidates=()),
            prefilter_rejections=(),
            eligibility=CandidateEligibilityDiagnostics(),
        )
        policy = SimpleNamespace(candidates=())
        with (
            patch("app.services.recsys.v3.recommender.get_redis", return_value=object()),
            patch("app.services.recsys.v3.recommender.get_active_serving_bundle", return_value=bundle),
            patch(
                "app.services.recsys.v3.recommender.build_user_runtime_profile",
                return_value=SimpleNamespace(bundle=profile),
            ),
            patch(
                "app.services.recsys.v3.recommender._load_published_candidates",
                return_value=((), "snapshot"),
            ),
            patch(
                "app.services.recsys.v3.recommender.onboarding_features_changed",
                return_value=True,
            ),
            patch(
                "app.services.recsys.v3.recommender.retrieve_lightfm_candidates",
                return_value=feature_only,
            ),
            patch(
                "app.services.recsys.v3.recommender._persist_feature_only_candidates"
            ) as persist,
            patch(
                "app.services.recsys.v3.recommender.run_cold_start_pipeline",
                return_value=cold_start,
            ),
            patch(
                "app.services.recsys.v3.recommender.evaluate_candidate_set",
                return_value=policy,
            ),
            patch("app.services.recsys.v3.recommender._policy_context"),
            patch("app.services.recsys.v3.recommender._persist_request_diagnostics"),
        ):
            response = get_recommendations(
                object(),
                user_id=101,
                mode=RecommendationMode.ALL,
                limit=20,
            )

        self.assertEqual(response.movie_ids, [])
        persist.assert_called_once_with(
            unittest.mock.ANY,
            bundle=bundle,
            profile=profile,
            candidates=feature_only,
            suppress_errors=True,
        )


class OnlineBaselineValidationTest(unittest.TestCase):
    def test_subscribed_only_allows_a_traced_empty_result(self) -> None:
        violations = validate_response(
            [],
            response_count=0,
            limit=20,
            excluded=frozenset(),
            subscribed_ids=set(),
            diagnostics={
                "candidate_count": 0,
                "final_count": 0,
                "candidate_path": "known_user_hybrid",
                "score_trace_complete": True,
                "attribution_valid": True,
            },
            allow_empty_candidates=True,
        )

        self.assertEqual(violations, [])

    def test_all_mode_still_rejects_an_empty_candidate_pool(self) -> None:
        violations = validate_response(
            [],
            response_count=0,
            limit=20,
            excluded=frozenset(),
            subscribed_ids=set(),
            diagnostics={
                "candidate_count": 0,
                "final_count": 0,
                "candidate_path": "known_user_hybrid",
                "score_trace_complete": True,
                "attribution_valid": True,
            },
        )

        self.assertEqual(violations, ["candidate_pool_empty"])


def _hash_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
