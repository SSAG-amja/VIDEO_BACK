from __future__ import annotations

import math
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from scipy.sparse import csr_matrix

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
    LongTermOntologyCandidate,
    ShortTermCandidate,
)
from app.services.recsys.v3.retrieval.long_term_ontology_retriever import (
    retrieve_long_term_ontology_candidates,
)
from app.services.recsys.v3.retrieval.ontology_feature_retriever import (
    build_budgeted_candidate_aggregate_rows,
    retrieve_budgeted_ontology_rows,
)
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize
from app.services.recsys.v3.retrieval.short_term_retriever import build_short_term_feature_rows
from app.services.recsys.v3.domain.schemas import (
    FeatureDirection,
    OttFilterMode,
    ProfileFeatureSignal,
)
from tests.test_v3_profile_builder import AS_OF, edge, signal


def catalog_movie(movie_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=movie_id,
        adult=False,
        title=f"movie-{movie_id}",
        title_ko=None,
        status=None,
        popularity=1.0,
        vote_average=7.0,
        vote_count=100,
        release_date=None,
    )


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

    def test_overlap_does_not_consume_short_term_only_floor(self) -> None:
        long_term = tuple(
            LongTermCandidate(movie_id=index, model_raw_score=float(101 - index), source_rank=index)
            for index in range(1, 101)
        )
        short_term = tuple(
            ShortTermCandidate(movie_id=index, short_term_raw_score=100.0, source_rank=index)
            for index in range(1, 26)
        ) + tuple(
            ShortTermCandidate(
                movie_id=1000 + index,
                short_term_raw_score=0.01,
                source_rank=25 + index,
            )
            for index in range(1, 11)
        )

        result = merge_candidates(long_term, short_term, drift_confidence=1.0)

        self.assertEqual(result.diagnostics.contextual_floor_count, 10)
        self.assertEqual(result.diagnostics.selected_short_only_count, 10)
        self.assertEqual(result.diagnostics.selected_overlap_count, 25)

    def test_long_term_ontology_lane_survives_into_first_100(self) -> None:
        long_term = tuple(
            LongTermCandidate(
                movie_id=index,
                model_raw_score=float(151 - index),
                source_rank=index,
            )
            for index in range(1, 151)
        )
        ontology = tuple(
            LongTermOntologyCandidate(
                movie_id=1000 + index,
                ontology_raw_score=float(101 - index),
                source_rank=index,
            )
            for index in range(1, 101)
        )

        result = merge_candidates(
            long_term,
            (),
            ontology,
            drift_confidence=0.0,
            limit=150,
        )

        first_100 = result.candidates[:100]
        ontology_count = sum(
            CandidateSource.LONG_TERM_ONTOLOGY in item.sources
            for item in first_100
        )
        self.assertGreaterEqual(ontology_count, 20)
        self.assertEqual(result.diagnostics.long_term_ontology_floor_count, 20)
        self.assertEqual(result.diagnostics.effective_model_weight, 0.65)
        self.assertEqual(
            result.diagnostics.effective_long_term_ontology_weight,
            0.35,
        )

    def test_model_and_long_term_ontology_scores_remain_separate(self) -> None:
        result = merge_candidates(
            (LongTermCandidate(10, 2.0, 1),),
            (),
            (LongTermOntologyCandidate(10, 3.0, 1),),
            drift_confidence=0.0,
            limit=1,
        )

        candidate = result.candidates[0]
        self.assertEqual(
            candidate.sources,
            (CandidateSource.MODEL, CandidateSource.LONG_TERM_ONTOLOGY),
        )
        self.assertEqual(candidate.model_raw_score, 2.0)
        self.assertEqual(candidate.long_term_ontology_raw_score, 3.0)
        self.assertEqual(candidate.normalized_long_term_score, 0.5)
        self.assertEqual(candidate.normalized_long_term_ontology_score, 0.5)
        self.assertEqual(result.diagnostics.model_ontology_agreement, 1.0)
        self.assertEqual(result.diagnostics.effective_model_weight, 0.55)
        self.assertEqual(result.diagnostics.effective_long_term_ontology_weight, 0.45)

    def test_collaborative_confidence_does_not_reduce_model_lane_weight(self) -> None:
        result = merge_candidates(
            (LongTermCandidate(10, 2.0, 1), LongTermCandidate(20, 1.0, 2)),
            (),
            (
                LongTermOntologyCandidate(10, 3.0, 1),
                LongTermOntologyCandidate(30, 2.0, 2),
            ),
            drift_confidence=0.0,
            collaborative_population_confidence=0.25,
            collaborative_user_evidence_confidence=1.0,
            collaborative_effective_confidence=0.25,
            limit=3,
        )

        self.assertEqual(result.diagnostics.base_model_weight, 0.60)
        self.assertAlmostEqual(result.diagnostics.effective_model_weight, 0.60)
        self.assertAlmostEqual(
            result.diagnostics.effective_long_term_ontology_weight,
            0.40,
        )
        self.assertEqual(result.diagnostics.collaborative_effective_confidence, 0.25)


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


class LongTermOntologyRetrieverTest(unittest.TestCase):
    @patch(
        "app.services.recsys.v3.retrieval.long_term_ontology_retriever."
        "load_short_term_candidate_rows",
        return_value=[(700, 2.5), (800, 1.5)],
    )
    @patch(
        "app.services.recsys.v3.retrieval.long_term_ontology_retriever."
        "validate_profile_build"
    )
    def test_uses_long_term_profile_and_keeps_source_score(
        self,
        validate_build,
        load_rows,
    ) -> None:
        profile = retrieval_profile()

        with patch(
            "app.services.recsys.v3.retrieval.initial_candidate_filter.load_movies_by_ids",
            return_value=[catalog_movie(700), catalog_movie(800)],
        ):
            result = retrieve_long_term_ontology_candidates(
                object(),
                ontology_build_id=22,
                profile=profile,
            )

        validate_build.assert_called_once_with(unittest.mock.ANY, 22)
        feature_rows = load_rows.call_args.kwargs["feature_rows"]
        self.assertTrue(feature_rows)
        self.assertEqual([item.movie_id for item in result.candidates], [700, 800])
        self.assertEqual(result.candidates[0].ontology_raw_score, 2.5)
        self.assertEqual(result.diagnostics.profile_feature_count, len(feature_rows))

    @patch(
        "app.services.recsys.v3.retrieval.long_term_ontology_retriever."
        "load_short_term_candidate_rows"
    )
    @patch(
        "app.services.recsys.v3.retrieval.long_term_ontology_retriever."
        "validate_profile_build"
    )
    def test_budgeted_artifact_avoids_runtime_graph_candidate_query(
        self,
        validate_build,
        load_rows,
    ) -> None:
        profile = retrieval_profile()
        artifact = budgeted_artifact()

        with patch(
            "app.services.recsys.v3.retrieval.initial_candidate_filter.load_movies_by_ids",
            return_value=[catalog_movie(10)],
        ):
            result = retrieve_long_term_ontology_candidates(
                object(),
                ontology_build_id=22,
                profile=profile,
                artifact=artifact,
                limit=3,
            )

        validate_build.assert_called_once_with(unittest.mock.ANY, 22)
        load_rows.assert_not_called()
        self.assertEqual(result.diagnostics.query_count, 2)
        self.assertEqual([item.movie_id for item in result.candidates], [10])


class OntologyFeatureRetrieverTest(unittest.TestCase):
    def test_missing_fields_do_not_transfer_their_budget_to_one_keyword(self) -> None:
        artifact = budgeted_artifact()
        features = (
            ProfileFeatureSignal(
                feature=FeatureName.GENRE,
                ref_id="10749",
                direction=FeatureDirection.POSITIVE,
                score=1.0,
            ),
            ProfileFeatureSignal(
                feature=FeatureName.KEYWORD,
                ref_id="818",
                direction=FeatureDirection.POSITIVE,
                score=1.0,
            ),
        )

        rows = retrieve_budgeted_ontology_rows(
            artifact,
            features=features,
            excluded_movie_ids=(),
            limit=3,
        )

        self.assertEqual([movie_id for movie_id, _score in rows], [20, 10, 30])
        self.assertAlmostEqual(dict(rows)[30], 0.02, places=6)
        self.assertLess(dict(rows)[30], dict(rows)[20])

    def test_detailed_aggregate_uses_same_bounded_feature_values(self) -> None:
        artifact = budgeted_artifact()
        rows = build_budgeted_candidate_aggregate_rows(
            artifact,
            candidate_movie_ids=(20, 30),
            profile_rows=(
                ("has_genre", "genre", "genre", "10749", "long_term", "positive", 1.0),
                ("has_keyword", "keyword", "keyword", "818", "long_term", "positive", 1.0),
            ),
        )

        by_movie_feature = {(row[0], row[1]): row[4] for row in rows}
        self.assertAlmostEqual(by_movie_feature[(20, "genre")], 0.25, places=6)
        self.assertAlmostEqual(by_movie_feature[(20, "keyword")], 0.02, places=6)
        self.assertAlmostEqual(by_movie_feature[(30, "keyword")], 0.02, places=6)


def budgeted_artifact() -> SimpleNamespace:
    movie_ids = np.asarray([10, 20, 30], dtype=np.int64)
    item_features = csr_matrix(
        np.asarray(
            [
                [0.0, 0.0, 0.0, 0.125, 0.0],
                [0.0, 0.0, 0.0, 0.25, 0.02],
                [0.0, 0.0, 0.0, 0.0, 0.02],
            ],
            dtype=np.float32,
        )
    )
    return SimpleNamespace(
        ontology_build_id=22,
        movie_ids=movie_ids,
        item_features=item_features,
        item_semantic_feature_index={"genre:10749": 3, "keyword:818": 4},
        movie_index=lambda movie_id: {10: 0, 20: 1, 30: 2}.get(movie_id),
        manifest={
            "feature_exports": {
                "item_representation_policy": "supported_identity_field_budgeted",
                "item_semantic_field_budgets": {"genre": 0.25, "keyword": 0.20},
                "item_keyword_weighting_policy": "normalized_idf",
            }
        },
    )


if __name__ == "__main__":
    unittest.main()
