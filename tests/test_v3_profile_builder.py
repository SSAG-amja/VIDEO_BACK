from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.services.recsys.v3.domain.behavior import SnapshotAction, SnapshotSignal
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.profiles.profile_builder import (
    GraphProfileEdge,
    aggregate_profile_features,
    assemble_user_runtime_profile,
    calculate_drift_confidence,
    long_term_decay,
    validate_profile_build,
)
from app.services.recsys.v3.domain.schemas import (
    FeatureDirection,
    OttFilterMode,
    ProfileFeatureSignal,
)


AS_OF = datetime(2026, 8, 20, 12, 0, 0)


def signal(
    movie_id: int,
    action: SnapshotAction,
    *,
    days_ago: int | None = 0,
) -> SnapshotSignal:
    return SnapshotSignal(
        user_id=7,
        movie_id=movie_id,
        action=action,
        occurred_at=None if days_ago is None else AS_OF - timedelta(days=days_ago),
    )


def edge(
    edge_id: int,
    movie_id: int,
    feature: FeatureName,
    ref_id: str,
    *,
    family_size: int = 1,
    strength: float = 1.0,
) -> GraphProfileEdge:
    relations = {
        FeatureName.GENRE: "has_genre",
        FeatureName.KEYWORD: "has_keyword",
        FeatureName.ACTOR: "has_actor",
        FeatureName.DIRECTOR: "has_director",
        FeatureName.THEME: "has_theme",
        FeatureName.MOOD: "has_mood",
    }
    return GraphProfileEdge(
        ontology_build_id=22,
        edge_id=edge_id,
        movie_id=movie_id,
        relation_type=relations[feature],
        feature=feature,
        ref_id=ref_id,
        edge_strength=strength,
        family_size=family_size,
    )


class RuntimeProfileBuilderTest(unittest.TestCase):
    def test_profile_separates_positive_negative_and_exclusions(self) -> None:
        signals = (
            signal(10, SnapshotAction.SAVED),
            signal(20, SnapshotAction.WATCHED, days_ago=2),
            signal(30, SnapshotAction.PINNED),
            signal(30, SnapshotAction.PASSED),
        )
        edges = {
            10: (edge(1, 10, FeatureName.GENRE, "10749"),),
            20: (edge(2, 20, FeatureName.GENRE, "18"),),
            30: (edge(3, 30, FeatureName.GENRE, "80"),),
        }

        result = assemble_user_runtime_profile(
            user_id=7,
            ontology_build_id=22,
            as_of=AS_OF,
            signals=signals,
            onboarding_genre_ids=frozenset({10749}),
            subscribed_ott_ids=frozenset({8}),
            edges_by_movie=edges,
            model_user_known=True,
            ott_mode=OttFilterMode.ALL,
        )

        long_term = result.bundle.long_term
        self.assertEqual(long_term.positive_movie_ids, frozenset({10, 20}))
        self.assertEqual(long_term.negative_movie_ids, frozenset({30}))
        self.assertEqual(long_term.excluded_movie_ids, frozenset({20, 30}))
        self.assertEqual(result.bundle.short_term.recent_positive_movie_ids, frozenset({10, 20}))
        self.assertEqual(result.bundle.short_term.recent_negative_movie_ids, frozenset({30}))
        self.assertGreater(result.bundle.short_term.drift_confidence, 0.0)
        negative = long_term.negative_features[0]
        self.assertEqual(negative.direction, FeatureDirection.NEGATIVE)
        self.assertEqual(negative.evidence[0].action, "passed")
        self.assertEqual(negative.evidence[0].edge_id, 3)

    def test_passed_only_never_creates_positive_short_term_profile(self) -> None:
        result = assemble_user_runtime_profile(
            user_id=7,
            ontology_build_id=22,
            as_of=AS_OF,
            signals=(signal(30, SnapshotAction.PASSED),),
            onboarding_genre_ids=frozenset(),
            subscribed_ott_ids=frozenset(),
            edges_by_movie={30: (edge(3, 30, FeatureName.THEME, "crime"),)},
            model_user_known=False,
            ott_mode=OttFilterMode.ALL,
        )

        short_term = result.bundle.short_term
        self.assertEqual(short_term.positive_features, ())
        self.assertEqual(short_term.recent_positive_movie_ids, frozenset())
        self.assertEqual(short_term.drift_confidence, 0.0)
        self.assertEqual(short_term.recent_negative_movie_ids, frozenset({30}))

    def test_feature_scoring_normalizes_large_families_and_keeps_edge_provenance(self) -> None:
        features, diagnostics = aggregate_profile_features(
            (signal(10, SnapshotAction.SAVED),),
            edges_by_movie={
                10: (
                    edge(1, 10, FeatureName.GENRE, "18", family_size=2),
                    edge(2, 10, FeatureName.GENRE, "35", family_size=2),
                    edge(3, 10, FeatureName.THEME, "healing", strength=0.8),
                )
            },
            ontology_build_id=22,
            direction=FeatureDirection.POSITIVE,
            as_of=AS_OF,
            decay=long_term_decay,
            top_k={
                "genre": 6,
                "keyword": 30,
                "actor": 30,
                "director": 12,
                "theme": 12,
                "mood": 10,
            },
        )

        genre = next(item for item in features if item.feature == FeatureName.GENRE)
        theme = next(item for item in features if item.feature == FeatureName.THEME)
        self.assertAlmostEqual(genre.score, 1.0 / (2.0**0.5), places=7)
        self.assertEqual(theme.score, 0.8)
        self.assertEqual(theme.contribution_count, 1)
        self.assertEqual(theme.evidence[0].relation_type, "has_theme")
        self.assertEqual(len(diagnostics), 6)

    def test_top_k_and_evidence_count_are_bounded(self) -> None:
        movie_edges = tuple(
            edge(index + 1, 10, FeatureName.GENRE, str(index), family_size=8)
            for index in range(8)
        )
        repeated_signals = tuple(signal(10, SnapshotAction.SAVED) for _ in range(12))
        features, diagnostics = aggregate_profile_features(
            repeated_signals,
            edges_by_movie={10: movie_edges},
            ontology_build_id=22,
            direction=FeatureDirection.POSITIVE,
            as_of=AS_OF,
            decay=long_term_decay,
            top_k={
                "genre": 6,
                "keyword": 30,
                "actor": 30,
                "director": 12,
                "theme": 12,
                "mood": 10,
            },
        )

        genres = [item for item in features if item.feature == FeatureName.GENRE]
        genre_diagnostics = next(
            item for item in diagnostics if item.feature == FeatureName.GENRE
        )
        self.assertEqual(len(genres), 6)
        self.assertEqual(genre_diagnostics.dropped_value_count, 2)
        self.assertTrue(all(len(item.evidence) == 8 for item in genres))
        self.assertTrue(all(item.contribution_count == 12 for item in genres))

    def test_drift_increases_when_recent_features_differ_from_history(self) -> None:
        recent = (
            ProfileFeatureSignal(
                feature=FeatureName.GENRE,
                ref_id="10749",
                direction=FeatureDirection.POSITIVE,
                score=1.0,
            ),
        )
        historical = (
            ProfileFeatureSignal(
                feature=FeatureName.GENRE,
                ref_id="80",
                direction=FeatureDirection.POSITIVE,
                score=1.0,
            ),
        )

        confidence, components = calculate_drift_confidence(
            recent_positive=recent,
            historical_positive=historical,
            recent_positive_action_count=1,
            recent_negative_action_count=0,
        )

        self.assertEqual(confidence, 0.2)
        self.assertEqual(components["novelty"], 1.0)

    def test_profile_build_requires_successful_v3_graph(self) -> None:
        class FakeSession:
            def __init__(self, build: OntologyBuild) -> None:
                self.build = build

            def get(self, _model: object, _build_id: int) -> OntologyBuild:
                return self.build

        successful = OntologyBuild(
            id=22,
            engine_name="v3",
            schema_version="v3.0",
            version="v3.0.0",
            status="success",
            is_active=False,
            source_hash="source",
        )
        self.assertIs(validate_profile_build(FakeSession(successful), 22), successful)  # type: ignore[arg-type]

        v2 = OntologyBuild(
            id=3,
            engine_name="v2",
            schema_version="v2",
            version="v2.0.0",
            status="success",
            is_active=True,
            source_hash="source",
        )
        with self.assertRaisesRegex(ValueError, "V3 ontology"):
            validate_profile_build(FakeSession(v2), 3)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
