from __future__ import annotations

import math
import unittest
from dataclasses import replace

from app.services.recsys.v3.domain.behavior import SnapshotAction
from app.services.recsys.v3.retrieval.candidate_merger import merge_candidates
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.retrieval.ontology_analyzer import (
    assemble_candidate_ontology_analyses,
    build_profile_rows,
)
from app.services.recsys.v3.profiles.profile_builder import assemble_user_runtime_profile
from app.services.recsys.v3.profiles.profile_builder import build_onboarding_feature_signals
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateSource,
    LongTermCandidate,
    ShortTermCandidate,
)
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize
from app.services.recsys.v3.retrieval.short_term_retriever import build_short_term_feature_rows
from app.services.recsys.v3.domain.schemas import OttFilterMode
from tests.test_v3_profile_builder import AS_OF, edge, signal


def retrieval_profile():
    result = assemble_user_runtime_profile(
        user_id=7,
        ontology_build_id=22,
        as_of=AS_OF,
        signals=(
            signal(10, SnapshotAction.SAVED),
            signal(20, SnapshotAction.WATCHED, days_ago=2),
            signal(30, SnapshotAction.PASSED),
        ),
        onboarding_genre_ids=frozenset({18}),
        subscribed_ott_ids=frozenset({8, 9}),
        edges_by_movie={
            10: (
                edge(1, 10, FeatureName.GENRE, "10749"),
                edge(2, 10, FeatureName.ACTOR, "55"),
            ),
            20: (edge(3, 20, FeatureName.THEME, "healing"),),
            30: (edge(4, 30, FeatureName.MOOD, "tense"),),
        },
        model_user_known=True,
        ott_mode=OttFilterMode.ALL,
    )
    return result.bundle


class ScoreNormalizerTest(unittest.TestCase):
    def test_percentile_normalization_is_deterministic_and_neutral_for_equal_scores(self) -> None:
        self.assertEqual(percentile_normalize({30: 2.0}), {30: 0.5})
        self.assertEqual(percentile_normalize({30: 2.0, 10: 2.0}), {30: 0.5, 10: 0.5})
        normalized = percentile_normalize({30: 3.0, 20: 2.0, 10: 1.0})
        self.assertEqual(normalized, {10: 0.0, 20: 0.5, 30: 1.0})


class CandidateMergerTest(unittest.TestCase):
    def test_no_drift_keeps_model_top_100_when_sources_do_not_overlap(self) -> None:
        long_term = tuple(
            LongTermCandidate(movie_id=index, model_raw_score=float(101 - index), source_rank=index)
            for index in range(1, 101)
        )
        short_term = tuple(
            ShortTermCandidate(movie_id=1000 + index, short_term_raw_score=float(101 - index), source_rank=index)
            for index in range(1, 101)
        )
        result = merge_candidates(long_term, short_term, drift_confidence=0.0)

        self.assertEqual(len(result.candidates), 100)
        self.assertTrue(all(item.sources == (CandidateSource.MODEL,) for item in result.candidates))
        self.assertEqual(result.diagnostics.contextual_floor_count, 0)
        self.assertEqual(result.diagnostics.selected_model_only_count, 100)
        self.assertEqual(result.diagnostics.selected_short_only_count, 0)
        self.assertEqual(result.diagnostics.selected_overlap_count, 0)

    def test_no_drift_tie_prefers_model_source_even_when_short_movie_id_is_lower(self) -> None:
        result = merge_candidates(
            (
                LongTermCandidate(movie_id=100, model_raw_score=2.0, source_rank=1),
                LongTermCandidate(movie_id=200, model_raw_score=1.0, source_rank=2),
            ),
            (ShortTermCandidate(movie_id=1, short_term_raw_score=1.0, source_rank=1),),
            drift_confidence=0.0,
            limit=2,
        )
        self.assertEqual([item.movie_id for item in result.candidates], [100, 200])

    def test_strong_drift_reserves_short_term_candidates(self) -> None:
        long_term = tuple(
            LongTermCandidate(movie_id=index, model_raw_score=float(101 - index), source_rank=index)
            for index in range(1, 101)
        )
        short_term = tuple(
            ShortTermCandidate(movie_id=1000 + index, short_term_raw_score=float(101 - index), source_rank=index)
            for index in range(1, 101)
        )
        result = merge_candidates(long_term, short_term, drift_confidence=1.0)
        short_selected = [
            item
            for item in result.candidates
            if CandidateSource.SHORT_TERM_CONTEXT in item.sources
        ]

        self.assertEqual(result.diagnostics.drift_weight, 0.45)
        self.assertEqual(result.diagnostics.contextual_floor_count, 25)
        self.assertGreaterEqual(len(short_selected), 25)
        self.assertEqual(result.diagnostics.selected_short_only_count, len(short_selected))
        self.assertEqual(len(result.candidates), 100)

    def test_overlapping_candidate_keeps_source_scores_separate(self) -> None:
        result = merge_candidates(
            (LongTermCandidate(10, 2.0, 1), LongTermCandidate(20, 1.0, 2)),
            (ShortTermCandidate(10, 1.0, 2), ShortTermCandidate(30, 2.0, 1)),
            drift_confidence=0.5,
            limit=3,
        )
        shared = next(item for item in result.candidates if item.movie_id == 10)
        self.assertEqual(shared.sources, (CandidateSource.MODEL, CandidateSource.SHORT_TERM_CONTEXT))
        self.assertEqual(shared.model_raw_score, 2.0)
        self.assertEqual(shared.short_term_raw_score, 1.0)
        self.assertEqual(result.diagnostics.selected_overlap_count, 1)


class OntologyAnalyzerTest(unittest.TestCase):
    def test_profile_rows_keep_scope_direction_and_actor_relation(self) -> None:
        rows = build_profile_rows(retrieval_profile())
        self.assertTrue(
            any(
                relation == "has_actor"
                and feature == "actor"
                and node_type == "person"
                and ref_id == "55"
                and scope == "short_term"
                and direction == "positive"
                for relation, feature, node_type, ref_id, scope, direction, _score in rows
            )
        )
        self.assertTrue(any(direction == "negative" for *_prefix, direction, _score in rows))

    def test_cold_analysis_includes_explicit_onboarding_genre(self) -> None:
        profile = retrieval_profile()

        normal_rows = build_profile_rows(profile)
        cold_rows = build_profile_rows(profile, include_onboarding=True)

        expected = ("has_genre", "genre", "genre", "18", "long_term", "positive")
        self.assertFalse(any(row[:6] == expected for row in normal_rows))
        self.assertTrue(any(row[:6] == expected and row[6] == 1.0 for row in cold_rows))

    def test_explicit_genre_keeps_at_least_full_onboarding_weight(self) -> None:
        profile = retrieval_profile()
        derived = replace(
            profile.long_term.positive_features[0],
            feature=FeatureName.GENRE,
            ref_id="18",
            score=0.25,
            raw_score=0.25,
        )
        onboarding = replace(profile.onboarding, derived_feature_priors=(derived,))

        signals = build_onboarding_feature_signals(onboarding)

        genre = next(item for item in signals if item.feature == FeatureName.GENRE)
        self.assertEqual(genre.score, 1.0)
        self.assertEqual(genre.raw_score, 1.0)

    def test_analysis_separates_scope_direction_type_and_ott(self) -> None:
        analyses = assemble_candidate_ontology_analyses(
            candidate_movie_ids=(100, 200),
            aggregate_rows=(
                (100, "genre", "long_term", "positive", 2.0, 1.0, 2),
                (100, "genre", "short_term", "positive", 1.5, 1.0, 2),
                (100, "mood", "long_term", "negative", 0.7, 0.7, 1),
                (200, "theme", "short_term", "negative", 0.4, 0.4, 1),
            ),
            streaming_ott_rows=((100, 8), (100, 15), (200, 9)),
            subscribed_ott_ids=frozenset({8, 9}),
        )
        first = analyses[0]
        genre = next(item for item in first.type_scores if item.feature == FeatureName.GENRE)
        mood = next(item for item in first.type_scores if item.feature == FeatureName.MOOD)

        self.assertAlmostEqual(genre.long_positive_score, 1.0 + math.log(2.0), places=7)
        self.assertGreater(genre.short_positive_score, 0.0)
        self.assertEqual(mood.long_negative_score, 0.7)
        self.assertEqual(first.ott.streaming_ott_ids, frozenset({8, 15}))
        self.assertEqual(first.ott.subscribed_streaming_ott_ids, frozenset({8}))
        self.assertEqual(len(first.type_scores), 6)


class ShortTermRetrieverTest(unittest.TestCase):
    def test_short_term_rows_only_use_recent_positive_features(self) -> None:
        profile = retrieval_profile()
        rows = build_short_term_feature_rows(profile.short_term.positive_features)
        relations = {row[0] for row in rows}
        refs = {row[3] for row in rows}

        self.assertIn("has_genre", relations)
        self.assertIn("has_actor", relations)
        self.assertNotIn("has_mood", relations)
        self.assertNotIn("tense", refs)


if __name__ == "__main__":
    unittest.main()
