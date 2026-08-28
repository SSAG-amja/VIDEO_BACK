from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from app.models.movie import Movie
from app.services.recsys.v3.cold_start.cold_start_merger import merge_cold_start_candidates
from app.services.recsys.v3.cold_start.cold_start_pipeline import (
    _feature_only_model_weight,
    run_cold_start_pipeline,
)
from app.services.recsys.v3.retrieval.eligibility_schemas import CandidateEligibilityDiagnostics
from app.services.recsys.v3.cold_start.cold_start_retriever import retrieve_cold_start_candidates
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.policy.policy_engine import (
    _catalog_trust_penalty,
    _negative_penalty,
    _quality_adjustment,
    evaluate_policy_candidates,
)
from app.services.recsys.v3.policy.policy_schemas import (
    HardFilterReason,
    MoviePolicyMetadata,
    PolicyAdjustmentSettings,
    PolicyComponentWeights,
    PolicyRequestContext,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateFeatureSet,
    CandidateMergeDiagnostics,
    CandidateMergeResult,
    CandidateOntologyAnalysis,
    CandidateSource,
    ColdStartCandidate,
    ColdStartMergeDiagnostics,
    ColdStartMergeResult,
    ColdStartRetrievalDiagnostics,
    ColdStartRetrievalResult,
    ColdStartStrategy,
    LongTermCandidate,
    MergedCandidate,
    OntologyAnalysisResult,
    OntologyAnalyzerDiagnostics,
    OntologyTypeScore,
    OttCandidateEvidence,
    RetrievalPipelineResult,
    ShortTermRetrievalDiagnostics,
    ShortTermRetrievalResult,
)
from app.services.recsys.v3.domain.schemas import OttFilterMode
from app.services.recsys.v3.domain.schemas import ShortTermPreferenceState
from tests.test_v3_profile_builder import AS_OF
from tests.test_v3_retrieval import retrieval_profile


def merged(movie_id: int, rank: int, score: float = 0.8) -> MergedCandidate:
    return MergedCandidate(
        movie_id=movie_id,
        sources=(CandidateSource.MODEL,),
        selection_rank=rank,
        candidate_selection_score=score,
        model_raw_score=score,
        normalized_long_term_score=score,
        model_source_rank=rank,
    )


def short_merged(movie_id: int, rank: int, score: float = 0.1) -> MergedCandidate:
    return MergedCandidate(
        movie_id=movie_id,
        sources=(CandidateSource.SHORT_TERM_CONTEXT,),
        selection_rank=rank,
        candidate_selection_score=score,
        normalized_short_term_score=score,
        short_term_raw_score=score,
        short_term_source_rank=rank,
    )


def overlap_merged(movie_id: int, rank: int, score: float = 0.9) -> MergedCandidate:
    return MergedCandidate(
        movie_id=movie_id,
        sources=(CandidateSource.MODEL, CandidateSource.SHORT_TERM_CONTEXT),
        selection_rank=rank,
        candidate_selection_score=score,
        model_raw_score=score,
        normalized_long_term_score=score,
        model_source_rank=rank,
        normalized_short_term_score=score,
        short_term_raw_score=score,
        short_term_source_rank=rank,
    )


def analysis(
    movie_id: int,
    *,
    subscribed: bool = False,
    genre: str | None = None,
    negative_score: float = 0.0,
) -> CandidateOntologyAnalysis:
    type_scores = tuple(
        OntologyTypeScore(
            feature=feature,
            long_negative_score=negative_score if feature == FeatureName.DIRECTOR else 0.0,
        )
        for feature in (
            FeatureName.GENRE,
            FeatureName.KEYWORD,
            FeatureName.ACTOR,
            FeatureName.DIRECTOR,
            FeatureName.THEME,
            FeatureName.MOOD,
        )
    )
    return CandidateOntologyAnalysis(
        movie_id=movie_id,
        type_scores=type_scores,
        long_positive_total=0.0,
        long_negative_total=negative_score,
        short_positive_total=0.0,
        short_negative_total=0.0,
        ott=OttCandidateEvidence(
            streaming_ott_ids=frozenset({8}) if subscribed else frozenset(),
            subscribed_streaming_ott_ids=frozenset({8}) if subscribed else frozenset(),
        ),
        repetition_features=CandidateFeatureSet(
            genre=frozenset({genre}) if genre else frozenset(),
        ),
    )


def movie(movie_id: int, *, status: str = "개봉") -> Movie:
    return Movie(
        id=movie_id,
        adult=False,
        title=f"movie-{movie_id}",
        status=status,
        popularity=10.0,
        vote_average=7.0,
        vote_count=100,
    )


def retrieval(
    movie_ids: tuple[int, ...],
    analyses: tuple[CandidateOntologyAnalysis, ...],
    candidates: tuple[MergedCandidate, ...] | None = None,
) -> RetrievalPipelineResult:
    candidate_values = candidates or tuple(
        merged(movie_id, rank) for rank, movie_id in enumerate(movie_ids, 1)
    )
    return RetrievalPipelineResult(
        short_term=ShortTermRetrievalResult(
            candidates=(),
            diagnostics=ShortTermRetrievalDiagnostics(
                ontology_build_id=22,
                profile_feature_count=0,
                excluded_movie_count=0,
                candidate_count=0,
                elapsed_seconds=0.0,
                query_count=1,
            ),
        ),
        merged=CandidateMergeResult(
            candidates=candidate_values,
            diagnostics=CandidateMergeDiagnostics(
                long_term_source_count=len(candidate_values),
                short_term_source_count=0,
                raw_union_count=len(candidate_values),
                selected_count=len(candidate_values),
                drift_confidence=0.0,
                drift_weight=0.0,
                contextual_floor_count=0,
            ),
        ),
        ontology=OntologyAnalysisResult(
            candidates=analyses,
            diagnostics=OntologyAnalyzerDiagnostics(
                ontology_build_id=22,
                candidate_count=len(analyses),
                matched_candidate_count=0,
                aggregate_row_count=0,
                repetition_feature_row_count=0,
                streaming_ott_row_count=0,
                query_count=1,
                elapsed_seconds=0.0,
            ),
        ),
        elapsed_seconds=0.0,
    )


class PolicyHardFilterTest(unittest.TestCase):
    def test_genre_only_cold_start_rejects_zero_vote_candidates(self) -> None:
        profile = retrieval_profile()
        result_input = retrieval((40,), (analysis(40),))
        zero_vote = movie(40)
        zero_vote.vote_count = 0
        with patch(
            "app.services.recsys.v3.policy.policy_engine.load_movies_by_ids",
            return_value=[zero_vote],
        ):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(
                    as_of=AS_OF,
                    limit=10,
                    genre_only_cold_start=True,
                ),
            )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.rejections[0].reasons,
            (HardFilterReason.COLD_START_NO_VOTES,),
        )

    def test_subscribed_mode_does_not_fallback_and_keeps_filter_reasons(self) -> None:
        profile = retrieval_profile()
        profile = replace(
            profile,
            serving_context=replace(profile.serving_context, ott_mode=OttFilterMode.SUBSCRIBED_ONLY),
        )
        movie_ids = (20, 30, 40, 50, 60)
        result_input = retrieval(
            movie_ids,
            tuple(
                analysis(movie_id, subscribed=movie_id in {20, 30, 50, 60})
                for movie_id in movie_ids
            ),
        )
        rows = [movie(movie_id, status="취소됨" if movie_id == 60 else "개봉") for movie_id in movie_ids]
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(
                    as_of=AS_OF,
                    limit=10,
                    session_exposed_movie_ids=frozenset({50}),
                ),
            )

        self.assertEqual(result.candidates, ())
        reasons = {item.movie_id: set(item.reasons) for item in result.rejections}
        self.assertIn(HardFilterReason.WATCHED, reasons[20])
        self.assertIn(HardFilterReason.PASSED, reasons[30])
        self.assertIn(HardFilterReason.NOT_ON_SUBSCRIBED_OTT, reasons[40])
        self.assertIn(HardFilterReason.SESSION_EXPOSED, reasons[50])
        self.assertIn(HardFilterReason.BLOCKED_STATUS, reasons[60])


class ColdStartTest(unittest.TestCase):
    def test_onboarding_graph_candidates_distinguish_ontology_cold_items(self) -> None:
        profile = retrieval_profile()
        with (
            patch("app.services.recsys.v3.cold_start.cold_start_retriever.validate_profile_build"),
            patch(
                "app.services.recsys.v3.cold_start.cold_start_retriever.load_cold_start_candidate_rows",
                return_value=[
                    (101, 3.0, 0.2, 0.8, 0.7, 1.0, True),
                    (102, 2.0, 0.0, 0.6, 0.5, 0.8, True),
                ],
            ),
        ):
            result = retrieve_cold_start_candidates(
                object(),
                ontology_build_id=22,
                profile=profile,
                model_known_movie_ids=frozenset({101}),
                limit=10,
            )

        self.assertEqual(result.diagnostics.strategy, ColdStartStrategy.ONTOLOGY_RULE)
        self.assertEqual(result.candidates[0].source, CandidateSource.COLD_START)
        self.assertEqual(result.candidates[0].overview_support_score, 0.2)
        self.assertEqual(result.candidates[0].rule_selection_score, 0.8)
        self.assertEqual(result.candidates[0].quality_score, 0.7)
        self.assertTrue(result.candidates[0].trusted_quality)
        self.assertEqual(result.candidates[1].source, CandidateSource.ONTOLOGY_COLD_ITEM)
        self.assertEqual(result.diagnostics.ontology_cold_item_count, 1)

    def test_favorite_onboarding_keeps_bounded_graph_lookup(self) -> None:
        profile = retrieval_profile()
        profile = replace(
            profile,
            onboarding=replace(profile.onboarding, favorite_movie_ids=frozenset({10})),
        )
        with (
            patch("app.services.recsys.v3.cold_start.cold_start_retriever.validate_profile_build"),
            patch(
                "app.services.recsys.v3.cold_start.cold_start_retriever.load_short_term_candidate_rows",
                return_value=[(101, 3.0)],
            ) as bounded_lookup,
            patch(
                "app.services.recsys.v3.cold_start.cold_start_retriever.load_cold_start_candidate_rows"
            ) as overview_lookup,
        ):
            result = retrieve_cold_start_candidates(
                object(),
                ontology_build_id=22,
                profile=profile,
                model_known_movie_ids=frozenset({101}),
                limit=10,
            )

        bounded_lookup.assert_called_once()
        overview_lookup.assert_not_called()
        self.assertIn(10, bounded_lookup.call_args.kwargs["excluded_movie_ids"])
        self.assertEqual(result.candidates[0].overview_support_score, 0.0)

    def test_pipeline_removes_favorites_from_feature_only_model_candidates(self) -> None:
        profile = replace(
            retrieval_profile(),
            onboarding=replace(
                retrieval_profile().onboarding,
                favorite_movie_ids=frozenset({10}),
            ),
        )
        retrieval_result = ColdStartRetrievalResult(
            candidates=(),
            diagnostics=ColdStartRetrievalDiagnostics(
                ontology_build_id=22,
                strategy=ColdStartStrategy.ONTOLOGY_RULE,
                profile_feature_count=1,
                excluded_movie_count=1,
                candidate_count=0,
                ontology_cold_item_count=0,
                query_count=2,
                elapsed_seconds=0.0,
            ),
        )
        merged_result = ColdStartMergeResult(
            candidates=(),
            diagnostics=ColdStartMergeDiagnostics(
                feature_only_model_count=1,
                rule_candidate_count=0,
                raw_union_count=1,
                selected_count=0,
                feature_only_model_weight=1.0,
            ),
        )
        ontology_result = OntologyAnalysisResult(
            candidates=(),
            diagnostics=OntologyAnalyzerDiagnostics(
                ontology_build_id=22,
                candidate_count=0,
                matched_candidate_count=0,
                aggregate_row_count=0,
                repetition_feature_row_count=0,
                streaming_ott_row_count=0,
                query_count=0,
                elapsed_seconds=0.0,
            ),
        )
        model_candidates = (
            LongTermCandidate(movie_id=10, model_raw_score=2.0, source_rank=1),
            LongTermCandidate(movie_id=11, model_raw_score=1.0, source_rank=2),
        )
        with (
            patch(
                "app.services.recsys.v3.cold_start.cold_start_pipeline.retrieve_cold_start_candidates",
                return_value=retrieval_result,
            ),
            patch(
                "app.services.recsys.v3.cold_start.cold_start_pipeline.merge_cold_start_candidates",
                return_value=merged_result,
            ) as merge,
            patch(
                "app.services.recsys.v3.cold_start.cold_start_pipeline.select_eligible_candidates",
                return_value=SimpleNamespace(
                    candidates=merged_result.candidates,
                    rejections=(),
                    diagnostics=CandidateEligibilityDiagnostics(
                        input_candidate_count=len(merged_result.candidates),
                        inspected_candidate_count=len(merged_result.candidates),
                        selected_candidate_count=len(merged_result.candidates),
                    ),
                ),
            ),
            patch(
                "app.services.recsys.v3.cold_start.cold_start_pipeline.analyze_candidates",
                return_value=ontology_result,
            ),
        ):
            run_cold_start_pipeline(
                object(),
                ontology_build_id=22,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
                feature_only_model_candidates=model_candidates,
            )

        self.assertEqual(
            tuple(item.movie_id for item in merge.call_args.args[0]),
            (11,),
        )

    def test_feature_only_model_and_rule_scores_remain_separate(self) -> None:
        result = merge_cold_start_candidates(
            (
                LongTermCandidate(movie_id=101, model_raw_score=2.0, source_rank=1),
                LongTermCandidate(movie_id=102, model_raw_score=1.0, source_rank=2),
            ),
            (
                ColdStartCandidate(
                    movie_id=101,
                    raw_score=4.0,
                    source_rank=1,
                    source=CandidateSource.COLD_START,
                    rule_selection_score=0.6,
                    quality_score=0.7,
                    genre_relevance_score=0.8,
                    trusted_quality=True,
                ),
                ColdStartCandidate(
                    movie_id=103,
                    raw_score=3.0,
                    source_rank=2,
                    source=CandidateSource.ONTOLOGY_COLD_ITEM,
                ),
            ),
        )
        overlap = next(item for item in result.candidates if item.movie_id == 101)
        self.assertEqual(
            overlap.sources,
            (CandidateSource.FEATURE_ONLY_MODEL, CandidateSource.COLD_START),
        )
        self.assertEqual(overlap.model_raw_score, 2.0)
        self.assertEqual(overlap.cold_start_raw_score, 4.0)
        self.assertEqual(overlap.cold_start_overview_support_score, 0.0)
        self.assertEqual(overlap.cold_start_rule_selection_score, 0.6)
        self.assertEqual(overlap.cold_start_quality_score, 0.7)
        self.assertEqual(overlap.cold_start_genre_relevance_score, 0.8)
        self.assertTrue(overlap.cold_start_trusted_quality)

    def test_rule_merge_ranks_by_selection_score_without_overwriting_semantic_raw(self) -> None:
        result = merge_cold_start_candidates(
            (),
            (
                ColdStartCandidate(
                    movie_id=101,
                    raw_score=10.0,
                    source_rank=1,
                    source=CandidateSource.COLD_START,
                    rule_selection_score=0.2,
                ),
                ColdStartCandidate(
                    movie_id=102,
                    raw_score=5.0,
                    source_rank=2,
                    source=CandidateSource.COLD_START,
                    rule_selection_score=0.8,
                ),
            ),
        )

        self.assertEqual([item.movie_id for item in result.candidates], [102, 101])
        self.assertEqual(result.candidates[1].cold_start_raw_score, 10.0)

    def test_rule_candidates_dominate_disjoint_feature_only_candidates(self) -> None:
        result = merge_cold_start_candidates(
            tuple(
                LongTermCandidate(movie_id=index, model_raw_score=float(11 - index), source_rank=index)
                for index in range(1, 11)
            ),
            tuple(
                ColdStartCandidate(
                    movie_id=100 + index,
                    raw_score=float(11 - index),
                    source_rank=index,
                    source=CandidateSource.COLD_START,
                    overview_support_score=0.1 if index == 1 else 0.0,
                )
                for index in range(1, 11)
            ),
            limit=10,
            feature_only_model_weight=0.30,
        )

        rule_count = sum(
            CandidateSource.COLD_START in item.sources for item in result.candidates
        )
        self.assertGreater(rule_count, 5)
        first_rule = next(
            item for item in result.candidates if CandidateSource.COLD_START in item.sources
        )
        self.assertEqual(first_rule.cold_start_overview_support_score, 0.1)

    def test_genre_only_profile_uses_lower_model_weight(self) -> None:
        profile = retrieval_profile()
        self.assertEqual(_feature_only_model_weight(profile), 0.15)

        with_favorite = replace(
            profile,
            onboarding=replace(profile.onboarding, favorite_movie_ids=frozenset({10})),
        )
        self.assertEqual(_feature_only_model_weight(with_favorite), 0.30)


class PolicyScoreTest(unittest.TestCase):
    def test_component_ablation_can_disable_ontology_without_changing_candidates(self) -> None:
        profile = retrieval_profile()
        ontology = replace(analysis(1), long_positive_total=2.0)
        result_input = retrieval((1,), (ontology,))
        with patch(
            "app.services.recsys.v3.policy.policy_engine.load_movies_by_ids",
            return_value=[movie(1)],
        ):
            current = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=1),
            )
            disabled = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=1),
                component_weights=PolicyComponentWeights(personal=1.0, ontology=0.0),
            )

        self.assertEqual(current.candidates[0].movie_id, disabled.candidates[0].movie_id)
        self.assertGreater(current.candidates[0].score.ontology_component, 0.0)
        self.assertEqual(disabled.candidates[0].score.ontology_component, 0.0)
        self.assertGreater(
            disabled.candidates[0].score.personal_component,
            current.candidates[0].score.personal_component,
        )

    def test_component_weights_must_sum_to_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to one"):
            PolicyComponentWeights(personal=0.75, ontology=0.0)

    def test_popularity_with_one_vote_cannot_dominate_reliable_quality(self) -> None:
        weak = MoviePolicyMetadata(
            movie_id=1,
            adult=False,
            title="weak",
            title_ko=None,
            status="개봉",
            popularity=1000.0,
            vote_average=10.0,
            vote_count=1,
            release_date=None,
        )
        reliable = replace(
            weak,
            movie_id=2,
            title="reliable",
            popularity=20.0,
            vote_average=7.0,
            vote_count=1000,
        )
        self.assertLess(_quality_adjustment(weak), _quality_adjustment(reliable))

    def test_catalog_trust_penalty_is_soft_and_proportional(self) -> None:
        settings = PolicyAdjustmentSettings(
            catalog_trust_penalty_max=0.05,
            catalog_trust_vote_threshold=20,
        )
        metadata = MoviePolicyMetadata(
            movie_id=1,
            adult=False,
            title="movie",
            title_ko=None,
            status="개봉",
            popularity=10.0,
            vote_average=7.0,
            vote_count=0,
            release_date=None,
        )

        self.assertEqual(
            _catalog_trust_penalty(
                metadata,
                settings=PolicyAdjustmentSettings(catalog_trust_penalty_max=0.0),
            ),
            0.0,
        )
        self.assertEqual(_catalog_trust_penalty(metadata), 0.05)
        self.assertEqual(_catalog_trust_penalty(metadata, settings=settings), 0.05)
        self.assertEqual(
            _catalog_trust_penalty(replace(metadata, vote_count=10), settings=settings),
            0.025,
        )
        self.assertEqual(
            _catalog_trust_penalty(replace(metadata, vote_count=20), settings=settings),
            0.0,
        )

    def test_catalog_trust_penalty_does_not_hard_filter_regular_candidates(self) -> None:
        profile = retrieval_profile()
        result_input = retrieval((40,), (analysis(40),))
        zero_vote = movie(40)
        zero_vote.vote_count = 0
        settings = PolicyAdjustmentSettings(catalog_trust_penalty_max=0.05)
        with patch(
            "app.services.recsys.v3.policy.policy_engine.load_movies_by_ids",
            return_value=[zero_vote],
        ):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=10),
                adjustment_settings=settings,
            )

        self.assertEqual([item.movie_id for item in result.candidates], [40])
        self.assertEqual(result.candidates[0].score.catalog_trust_penalty, 0.05)

    def test_semantic_negative_penalty_is_bounded(self) -> None:
        profile = retrieval_profile()
        penalty = _negative_penalty(analysis(1, negative_score=100.0), profile=profile, base_score=1.0)
        self.assertLessEqual(penalty, 0.2)
        self.assertGreater(penalty, 0.0)

    def test_semantic_negative_penalty_can_be_disabled_for_ablation(self) -> None:
        profile = retrieval_profile()
        penalty = _negative_penalty(
            analysis(1, negative_score=100.0),
            profile=profile,
            base_score=1.0,
            settings=PolicyAdjustmentSettings(
                negative_max_base_ratio=0.0,
                negative_max_absolute=0.0,
            ),
        )
        self.assertEqual(penalty, 0.0)

    def test_repeated_feature_is_deterministically_reranked(self) -> None:
        profile = retrieval_profile()
        movie_ids = (101, 102, 103)
        result_input = retrieval(
            movie_ids,
            (
                analysis(101, genre="crime"),
                analysis(102, genre="crime"),
                analysis(103, genre="romance"),
            ),
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        context = PolicyRequestContext(as_of=AS_OF, limit=3)
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            first = evaluate_policy_candidates(
                object(), retrieval=result_input, profile=profile, context=context
            )
            second = evaluate_policy_candidates(
                object(), retrieval=result_input, profile=profile, context=context
            )

        self.assertEqual([item.movie_id for item in first.candidates], [101, 103, 102])
        self.assertEqual(
            [item.movie_id for item in first.candidates],
            [item.movie_id for item in second.candidates],
        )
        self.assertGreater(first.candidates[2].score.repetition_penalty, 0.0)
        self.assertGreater(first.candidates[2].score.mmr_similarity_penalty, 0.0)

    def test_drift_preserves_short_term_lane_in_final_results(self) -> None:
        profile = replace(
            retrieval_profile(),
            short_term=replace(
                retrieval_profile().short_term,
                preference_state=ShortTermPreferenceState.DRIFT,
                drift_confidence=0.8,
            ),
        )
        movie_ids = (1, 2, 3, 4, 5, 6, 101, 102)
        candidates = tuple(
            merged(movie_id, rank, score=0.9)
            for rank, movie_id in enumerate(movie_ids[:6], 1)
        ) + tuple(
            short_merged(movie_id, rank, score=0.1)
            for rank, movie_id in enumerate(movie_ids[6:], 7)
        )
        result_input = retrieval(
            movie_ids,
            tuple(analysis(movie_id) for movie_id in movie_ids),
            candidates=candidates,
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=5),
            )

        short_count = sum(
            item.candidate.sources == (CandidateSource.SHORT_TERM_CONTEXT,)
            for item in result.candidates
        )
        self.assertGreaterEqual(short_count, 2)
        self.assertEqual(result.diagnostics.short_term_lane_target, 2)
        self.assertEqual(result.diagnostics.selected_short_term_only_count, 2)
        self.assertGreaterEqual(result.diagnostics.forced_short_term_only_count, 1)

    def test_stable_profile_does_not_force_short_term_lane(self) -> None:
        profile = replace(
            retrieval_profile(),
            short_term=replace(
                retrieval_profile().short_term,
                preference_state=ShortTermPreferenceState.STABLE,
                drift_confidence=0.8,
            ),
        )
        movie_ids = (1, 2, 3, 4, 5, 6, 101, 102)
        candidates = tuple(
            merged(movie_id, rank, score=0.9)
            for rank, movie_id in enumerate(movie_ids[:6], 1)
        ) + tuple(
            short_merged(movie_id, rank, score=0.1)
            for rank, movie_id in enumerate(movie_ids[6:], 7)
        )
        result_input = retrieval(
            movie_ids,
            tuple(analysis(movie_id) for movie_id in movie_ids),
            candidates=candidates,
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=5),
            )

        short_count = sum(
            CandidateSource.SHORT_TERM_CONTEXT in item.candidate.sources
            for item in result.candidates
        )
        self.assertEqual(short_count, 0)

    def test_drift_uses_available_short_term_candidates_only(self) -> None:
        profile = replace(
            retrieval_profile(),
            short_term=replace(
                retrieval_profile().short_term,
                preference_state=ShortTermPreferenceState.DRIFT,
                drift_confidence=1.0,
            ),
        )
        movie_ids = (1, 2, 3, 4, 5, 6, 101)
        candidates = tuple(
            merged(movie_id, rank, score=0.9)
            for rank, movie_id in enumerate(movie_ids[:6], 1)
        ) + (short_merged(101, 7, score=0.1),)
        result_input = retrieval(
            movie_ids,
            tuple(analysis(movie_id) for movie_id in movie_ids),
            candidates=candidates,
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=5),
            )

        short_count = sum(
            CandidateSource.SHORT_TERM_CONTEXT in item.candidate.sources
            for item in result.candidates
        )
        self.assertEqual(short_count, 1)

    def test_drift_does_not_bypass_hard_filter_for_short_term_lane(self) -> None:
        profile = replace(
            retrieval_profile(),
            short_term=replace(
                retrieval_profile().short_term,
                preference_state=ShortTermPreferenceState.DRIFT,
                drift_confidence=1.0,
            ),
        )
        movie_ids = (1, 2, 3, 4, 5, 6, 101, 102)
        candidates = tuple(
            merged(movie_id, rank, score=0.9)
            for rank, movie_id in enumerate(movie_ids[:6], 1)
        ) + tuple(
            short_merged(movie_id, rank, score=0.1)
            for rank, movie_id in enumerate(movie_ids[6:], 7)
        )
        result_input = retrieval(
            movie_ids,
            tuple(analysis(movie_id) for movie_id in movie_ids),
            candidates=candidates,
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        rows[-1].adult = True
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=5),
            )

        short_count = sum(
            CandidateSource.SHORT_TERM_CONTEXT in item.candidate.sources
            for item in result.candidates
        )
        self.assertEqual(short_count, 1)
        self.assertEqual(result.rejections[0].movie_id, 102)
        self.assertEqual(result.rejections[0].reasons, (HardFilterReason.ADULT,))

    def test_model_overlap_does_not_satisfy_short_term_only_lane(self) -> None:
        profile = replace(
            retrieval_profile(),
            short_term=replace(
                retrieval_profile().short_term,
                preference_state=ShortTermPreferenceState.DRIFT,
                drift_confidence=1.0,
            ),
        )
        movie_ids = (1, 2, 3, 4, 5, 101, 102)
        candidates = tuple(
            merged(movie_id, rank, score=0.9)
            for rank, movie_id in enumerate(movie_ids[:5], 1)
        ) + (
            overlap_merged(101, 6, score=0.9),
            short_merged(102, 7, score=0.1),
        )
        result_input = retrieval(
            movie_ids,
            tuple(analysis(movie_id) for movie_id in movie_ids),
            candidates=candidates,
        )
        rows = [movie(movie_id) for movie_id in movie_ids]
        with patch("app.services.recsys.v3.policy.policy_engine.load_movies_by_ids", return_value=rows):
            result = evaluate_policy_candidates(
                object(),
                retrieval=result_input,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=5),
            )

        self.assertIn(102, {item.movie_id for item in result.candidates})
        self.assertEqual(result.diagnostics.short_term_lane_target, 1)
        self.assertEqual(result.diagnostics.selected_short_term_only_count, 1)


if __name__ == "__main__":
    unittest.main()
