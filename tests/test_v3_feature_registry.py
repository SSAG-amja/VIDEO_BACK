from __future__ import annotations

import unittest
from datetime import datetime

import numpy as np
from scipy.sparse import csr_matrix

from app.jobs.recsys.v3.features.feature_builder import (
    default_item_feature_pruning_rules,
    export_item_features,
    graph_item_feature_definitions,
)
from app.jobs.recsys.v3.features.feature_schemas import (
    ItemFeatureExport,
    ItemFeatureFamilyDiagnostics,
    ItemFeatureManifest,
    ItemFeaturePruningRule,
)
from app.jobs.recsys.v3.diagnostics.item_feature_export_diagnostics import (
    build_export_diagnostics,
)
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.domain.feature_registry import (
    FEATURE_REGISTRY,
    ConsumerStatus,
    FeatureConsumer,
    FeatureName,
    SourceReadiness,
    features_for_consumer,
    get_feature_definition,
    validate_feature_registry,
)
from app.services.recsys.v3.domain.schemas import (
    FeatureCoverageDiagnostics,
    FeatureDirection,
    FeatureDropCount,
    LongTermProfile,
    OnboardingProfile,
    OttFilterMode,
    ProfileFeatureSignal,
    ProfileMaturity,
    ServingContext,
    ShortTermProfile,
    UserProfileBundle,
)


class FeatureRegistryTest(unittest.TestCase):
    def test_item_exporter_uses_only_registered_non_ott_graph_features(self) -> None:
        definitions = graph_item_feature_definitions()

        self.assertEqual(
            tuple(item.name for item in definitions),
            (
                FeatureName.GENRE,
                FeatureName.KEYWORD,
                FeatureName.ACTOR,
                FeatureName.DIRECTOR,
                FeatureName.THEME,
                FeatureName.MOOD,
            ),
        )
        self.assertTrue(
            all(
                item.consumer_status(FeatureConsumer.LIGHTFM_ITEM)
                != ConsumerStatus.DISABLED
                for item in definitions
            )
        )

    def test_item_exporter_pruning_defaults_are_explicit(self) -> None:
        rules = default_item_feature_pruning_rules()

        self.assertEqual(rules[FeatureName.ACTOR].min_movie_frequency, 5)
        self.assertEqual(rules[FeatureName.DIRECTOR].min_movie_frequency, 5)
        self.assertEqual(rules[FeatureName.KEYWORD].min_movie_frequency, 5)
        self.assertEqual(rules[FeatureName.KEYWORD].max_catalog_ratio, 0.5)
        self.assertEqual(rules[FeatureName.GENRE].min_movie_frequency, 1)
        self.assertIsNone(rules[FeatureName.THEME].max_catalog_ratio)

        with self.assertRaises(ValueError):
            ItemFeaturePruningRule(min_movie_frequency=0)
        with self.assertRaises(ValueError):
            ItemFeaturePruningRule(max_catalog_ratio=1.1)

    def test_item_exporter_rejects_a_v2_build(self) -> None:
        class FakeSession:
            def get(self, _model: object, _build_id: int) -> OntologyBuild:
                return OntologyBuild(
                    id=3,
                    engine_name="v2",
                    schema_version="v2",
                    version="v2.0.0",
                    status="success",
                    is_active=True,
                    source_hash="v2-source",
                )

        with self.assertRaisesRegex(ValueError, "requires a V3 ontology build"):
            export_item_features(FakeSession(), 3)  # type: ignore[arg-type]

    def test_full_export_diagnostics_reports_sparse_memory_and_coverage(self) -> None:
        coverage = FeatureCoverageDiagnostics(
            feature=FeatureName.GENRE,
            consumer=FeatureConsumer.LIGHTFM_ITEM,
            total_entity_count=2,
            covered_entity_count=2,
            source_value_count=1,
            retained_value_count=1,
            dropped_value_count=0,
            drop_counts=(),
        )
        family = ItemFeatureFamilyDiagnostics(
            feature=FeatureName.GENRE,
            relation_type="has_genre",
            source_edge_count=2,
            retained_edge_count=2,
            matrix_nnz=2,
            coverage=coverage,
        )
        matrix = csr_matrix(
            np.array(
                [
                    [1.0, 0.0, 1.0],
                    [0.0, 1.0, 1.0],
                ],
                dtype=np.float32,
            )
        )
        manifest = ItemFeatureManifest(
            exporter_version="test",
            ontology_build_id=22,
            ontology_engine_name="v3",
            ontology_schema_version="v3.0",
            ontology_source_hash="source",
            movie_count=2,
            feature_count=3,
            matrix_nnz=4,
            matrix_shape=(2, 3),
            movie_mapping_hash="a" * 64,
            feature_mapping_hash="b" * 64,
            export_hash="c" * 64,
            pruning_rules={},
            family_diagnostics=(family,),
        )
        export = ItemFeatureExport(
            movie_ids=(10, 20),
            movie_id_map={10: 0, 20: 1},
            feature_tokens=("movie:10", "movie:20", "genre:1"),
            feature_token_map={"movie:10": 0, "movie:20": 1, "genre:1": 2},
            item_features=matrix,
            manifest=manifest,
        )

        diagnostics = build_export_diagnostics(
            export,
            elapsed_seconds=1.25,
            initial_rss_bytes=100,
            final_rss_bytes=200,
            peak_rss_bytes=300,
        )

        self.assertEqual(diagnostics["matrix"]["shape"], [2, 3])
        self.assertEqual(diagnostics["matrix"]["nnz"], 4)
        self.assertGreater(diagnostics["memory"]["csr_bytes"], 0)
        self.assertEqual(
            diagnostics["manifest"]["family_diagnostics"][0]["movie_coverage_ratio"],
            1.0,
        )

    def test_registry_is_complete_and_namespaces_are_stable(self) -> None:
        validate_feature_registry()

        self.assertEqual(len(FEATURE_REGISTRY), len(FeatureName))
        self.assertEqual(get_feature_definition(FeatureName.GENRE).token(28), "genre:28")
        self.assertEqual(
            get_feature_definition(FeatureName.THEME).token("family_conflict"),
            "theme:family_conflict",
        )

    def test_actor_and_director_share_person_node_but_not_namespace(self) -> None:
        actor = get_feature_definition(FeatureName.ACTOR)
        director = get_feature_definition(FeatureName.DIRECTOR)

        self.assertEqual(actor.ontology_node_type, "person")
        self.assertEqual(director.ontology_node_type, "person")
        self.assertNotEqual(actor.namespace, director.namespace)
        self.assertIn(SourceReadiness.PENDING_V3_ONTOLOGY, {item.readiness for item in actor.sources})

    def test_ott_is_disabled_for_lightfm_and_required_for_serving(self) -> None:
        ott = get_feature_definition(FeatureName.OTT_STREAMING)

        self.assertEqual(
            ott.consumer_status(FeatureConsumer.LIGHTFM_ITEM),
            ConsumerStatus.DISABLED,
        )
        self.assertEqual(
            ott.consumer_status(FeatureConsumer.LIGHTFM_USER),
            ConsumerStatus.DISABLED,
        )
        self.assertEqual(
            ott.consumer_status(FeatureConsumer.SERVING_CONTEXT),
            ConsumerStatus.REQUIRED,
        )
        self.assertNotIn(
            FeatureName.OTT_STREAMING,
            {item.name for item in features_for_consumer(FeatureConsumer.LIGHTFM_ITEM, include_optional=True)},
        )


class ProfileSchemaTest(unittest.TestCase):
    def test_profile_bundle_keeps_ott_in_serving_context(self) -> None:
        now = datetime(2026, 8, 20)
        genre = ProfileFeatureSignal(
            feature=FeatureName.GENRE,
            ref_id="28",
            direction=FeatureDirection.POSITIVE,
            score=1.0,
            source_movie_ids=frozenset({10}),
            source_actions=("favorite",),
        )
        onboarding = OnboardingProfile(
            user_id=1,
            favorite_movie_ids=frozenset({10}),
            genre_ids=frozenset({28}),
            derived_feature_priors=(genre,),
        )
        long_term = LongTermProfile(
            user_id=1,
            as_of=now,
            maturity=ProfileMaturity.SPARSE,
            model_user_known=True,
            positive_movie_ids=frozenset({10}),
            negative_movie_ids=frozenset({11}),
            excluded_movie_ids=frozenset({11, 12}),
            positive_features=(genre,),
            positive_pair_count=1,
            passed_pair_count=1,
            watched_pair_count=1,
        )
        short_term = ShortTermProfile(
            user_id=1,
            as_of=now,
            window_action_count=1,
            drift_confidence=0.2,
            recent_positive_movie_ids=frozenset({10}),
            positive_features=(genre,),
        )
        serving_context = ServingContext(
            user_id=1,
            ott_mode=OttFilterMode.SUBSCRIBED_ONLY,
            availability_as_of=now,
            subscribed_ott_ids=frozenset({8}),
        )

        bundle = UserProfileBundle(
            user_id=1,
            onboarding=onboarding,
            long_term=long_term,
            short_term=short_term,
            serving_context=serving_context,
        )

        self.assertEqual(bundle.serving_context.subscribed_ott_ids, frozenset({8}))

    def test_ott_cannot_be_added_to_onboarding_feature_priors(self) -> None:
        ott = ProfileFeatureSignal(
            feature=FeatureName.OTT_STREAMING,
            ref_id="8",
            direction=FeatureDirection.POSITIVE,
            score=1.0,
        )

        with self.assertRaises(ValueError):
            OnboardingProfile(user_id=1, derived_feature_priors=(ott,))

    def test_long_term_profile_rejects_positive_negative_conflict(self) -> None:
        with self.assertRaises(ValueError):
            LongTermProfile(
                user_id=1,
                as_of=datetime(2026, 8, 20),
                maturity=ProfileMaturity.SPARSE,
                model_user_known=True,
                positive_movie_ids=frozenset({10}),
                negative_movie_ids=frozenset({10}),
                excluded_movie_ids=frozenset({10}),
            )

    def test_feature_coverage_requires_accounted_drop_counts(self) -> None:
        diagnostics = FeatureCoverageDiagnostics(
            feature=FeatureName.ACTOR,
            consumer=FeatureConsumer.LIGHTFM_ITEM,
            total_entity_count=100,
            covered_entity_count=90,
            source_value_count=50,
            retained_value_count=40,
            dropped_value_count=10,
            drop_counts=(FeatureDropCount(reason="low_frequency", count=10),),
        )

        self.assertEqual(diagnostics.retained_value_count, 40)


if __name__ == "__main__":
    unittest.main()
