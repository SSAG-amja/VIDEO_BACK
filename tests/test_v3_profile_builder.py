from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.services.recsys.v3.domain.behavior import SnapshotAction, SnapshotSignal
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.profiles.profile_builder import (
    GraphProfileEdge,
    aggregate_profile_features,
    assess_preference_shift,
    assemble_user_runtime_profile,
    long_term_decay,
    validate_profile_build,
)
from app.services.recsys.v3.domain.schemas import (
    FeatureDirection,
    OttFilterMode,
    ProfileFeatureSignal,
    ShortTermPreferenceState,
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


def profile_feature(feature: FeatureName, ref_id: str, score: float = 1.0) -> ProfileFeatureSignal:
    return ProfileFeatureSignal(
        feature=feature,
        ref_id=ref_id,
        direction=FeatureDirection.POSITIVE,
        score=score,
    )


class RuntimeProfileBuilderTest(unittest.TestCase):
    def test_long_term_decay_is_action_specific_and_continuous(self) -> None:
        recent_saved = long_term_decay(signal(10, SnapshotAction.SAVED), AS_OF)
        old_saved = long_term_decay(
            signal(10, SnapshotAction.SAVED, days_ago=60),
            AS_OF,
        )
        old_watched = long_term_decay(
            signal(10, SnapshotAction.WATCHED, days_ago=60),
            AS_OF,
        )
        very_old_saved = long_term_decay(
            signal(10, SnapshotAction.SAVED, days_ago=1_000),
            AS_OF,
        )

        self.assertEqual(recent_saved, 1.0)
        self.assertAlmostEqual(old_saved, 0.5, places=7)
        self.assertGreater(old_watched, old_saved)
        self.assertEqual(very_old_saved, 0.05)
        self.assertEqual(
            long_term_decay(signal(10, SnapshotAction.FAVORITE, days_ago=None), AS_OF),
            1.0,
        )
        self.assertEqual(
            long_term_decay(signal(10, SnapshotAction.SAVED, days_ago=None), AS_OF),
            0.25,
        )

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
        self.assertEqual(
            result.bundle.short_term.preference_state,
            ShortTermPreferenceState.INACTIVE,
        )
        self.assertEqual(result.bundle.short_term.drift_confidence, 0.0)
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

    def test_preference_shift_ignores_secondary_novelty_when_genre_is_stable(self) -> None:
        recent = (
            profile_feature(FeatureName.GENRE, "28"),
            profile_feature(FeatureName.ACTOR, "new-actor"),
        )
        historical = (
            profile_feature(FeatureName.GENRE, "28"),
            profile_feature(FeatureName.ACTOR, "old-actor"),
        )

        assessment = assess_preference_shift(
            recent_positive=recent,
            historical_positive=historical,
            recent_positive_movie_count=3,
            recent_positive_weight_sum=2.25,
            historical_positive_movie_count=8,
            recent_negative_action_count=0,
        )

        self.assertEqual(assessment.state, ShortTermPreferenceState.STABLE)
        self.assertAlmostEqual(assessment.semantic_distance, 0.15)
        self.assertEqual(assessment.drift_confidence, 0.0)
        self.assertEqual(assessment.components["genre_distance"], 0.0)
        self.assertEqual(assessment.components["actor_distance"], 1.0)

    def test_preference_shift_detects_primary_semantic_change(self) -> None:
        assessment = assess_preference_shift(
            recent_positive=(
                profile_feature(FeatureName.GENRE, "10749"),
                profile_feature(FeatureName.ACTOR, "shared-actor"),
            ),
            historical_positive=(
                profile_feature(FeatureName.GENRE, "80"),
                profile_feature(FeatureName.ACTOR, "shared-actor"),
            ),
            recent_positive_movie_count=3,
            recent_positive_weight_sum=2.25,
            historical_positive_movie_count=8,
            recent_negative_action_count=0,
        )

        self.assertEqual(assessment.state, ShortTermPreferenceState.DRIFT)
        self.assertAlmostEqual(assessment.drift_confidence, 0.76923077)

    def test_preference_shift_requires_accumulated_recent_evidence(self) -> None:
        assessment = assess_preference_shift(
            recent_positive=(profile_feature(FeatureName.GENRE, "10749"),),
            historical_positive=(profile_feature(FeatureName.GENRE, "80"),),
            recent_positive_movie_count=1,
            recent_positive_weight_sum=1.0,
            historical_positive_movie_count=8,
            recent_negative_action_count=0,
        )

        self.assertEqual(assessment.state, ShortTermPreferenceState.INACTIVE)
        self.assertEqual(assessment.drift_confidence, 0.0)

    def test_preference_shift_marks_recent_interest_when_history_is_sparse(self) -> None:
        assessment = assess_preference_shift(
            recent_positive=(profile_feature(FeatureName.GENRE, "10749"),),
            historical_positive=(profile_feature(FeatureName.GENRE, "80"),),
            recent_positive_movie_count=2,
            recent_positive_weight_sum=2.0,
            historical_positive_movie_count=2,
            recent_negative_action_count=0,
        )

        self.assertEqual(assessment.state, ShortTermPreferenceState.RECENT_INTEREST)
        self.assertEqual(assessment.drift_confidence, 0.0)

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
