from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from app.services.recsys.v3.retrieval.candidate_eligibility import select_eligible_candidates
from app.services.recsys.v3.retrieval.initial_candidate_filter import (
    build_initial_catalog_mask,
    select_initial_candidates,
)
from app.services.recsys.v3.policy.policy_schemas import HardFilterReason, PolicyRequestContext
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateSource,
    LongTermCandidate,
    MergedCandidate,
)
from app.services.recsys.v3.domain.schemas import OttFilterMode
from tests.test_v3_policy_engine import movie
from tests.test_v3_profile_builder import AS_OF
from tests.test_v3_retrieval import retrieval_profile


def candidate(movie_id: int, rank: int) -> MergedCandidate:
    score = max(0.0, 1.0 - (rank - 1) / 149)
    return MergedCandidate(
        movie_id=movie_id,
        sources=(CandidateSource.MODEL,),
        selection_rank=rank,
        candidate_selection_score=score,
        model_raw_score=score,
        normalized_long_term_score=score,
        model_source_rank=rank,
    )


def clean_profile():
    profile = retrieval_profile()
    return replace(
        profile,
        long_term=replace(
            profile.long_term,
            negative_movie_ids=frozenset(),
            excluded_movie_ids=frozenset(),
            passed_pair_count=0,
            watched_pair_count=0,
        ),
        short_term=replace(
            profile.short_term,
            recent_negative_movie_ids=frozenset(),
        ),
    )


class CandidateEligibilityTest(unittest.TestCase):
    def test_catalog_mask_separates_static_trust_from_request_filters(self) -> None:
        movies = [movie(movie_id) for movie_id in range(1, 5)]
        for item in movies:
            item.release_date = AS_OF.date() - timedelta(days=1_000)
            item.vote_count = 0
        movies[2].release_date = AS_OF.date() - timedelta(days=30)
        movies[3].adult = True

        with patch(
            "app.services.recsys.v3.retrieval.initial_candidate_filter.load_movies_by_ids",
            return_value=movies,
        ):
            result = build_initial_catalog_mask(
                object(),
                movie_ids=(1, 2, 3, 4),
                identity_supported_movie_ids=frozenset({1}),
                as_of=AS_OF,
                chunk_size=10,
            )

        self.assertEqual(result.eligible_item_mask.tolist(), [True, False, True, False])
        self.assertEqual(
            dict(result.rejection_counts),
            {
                HardFilterReason.ADULT.value: 1,
                HardFilterReason.UNTRUSTED_MATURE_COLD_ITEM.value: 2,
            },
        )

    def test_initial_filter_fills_limit_before_request_filtering(self) -> None:
        candidates = tuple(
            LongTermCandidate(
                movie_id=movie_id,
                model_raw_score=float(5 - movie_id),
                source_rank=movie_id,
            )
            for movie_id in range(1, 5)
        )
        movies = [movie(movie_id) for movie_id in range(1, 5)]
        movies[0].release_date = AS_OF.date() - timedelta(days=1_000)
        movies[0].vote_count = 0

        with patch(
            "app.services.recsys.v3.retrieval.initial_candidate_filter.load_movies_by_ids",
            return_value=movies,
        ):
            result = select_initial_candidates(
                object(),
                candidates=candidates,
                as_of=AS_OF,
                movie_identity_supported=lambda movie_id: movie_id != 1,
                limit=3,
            )

        self.assertEqual([item.movie_id for item in result.candidates], [2, 3, 4])
        self.assertEqual([item.source_rank for item in result.candidates], [1, 2, 3])
        self.assertEqual(result.inspected_candidate_count, 4)
        self.assertEqual(
            result.rejections[0].reasons,
            (HardFilterReason.UNTRUSTED_MATURE_COLD_ITEM,),
        )

    def test_long_term_cold_item_uses_release_age_and_vote_trust(self) -> None:
        profile = clean_profile()
        candidates = tuple(candidate(movie_id, movie_id) for movie_id in range(1, 7))
        movies = [movie(movie_id) for movie_id in range(1, 7)]
        movies[0].release_date = AS_OF.date() - timedelta(days=1_000)
        movies[0].vote_count = 19
        movies[1].release_date = AS_OF.date() - timedelta(days=1_000)
        movies[1].vote_count = 20
        movies[2].release_date = AS_OF.date() - timedelta(days=180)
        movies[2].vote_count = 0
        movies[3].release_date = AS_OF.date() - timedelta(days=181)
        movies[3].vote_count = 0
        movies[4].release_date = None
        movies[4].vote_count = 19
        movies[5].release_date = AS_OF.date() - timedelta(days=1_000)
        movies[5].vote_count = 0

        with patch(
            "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
            return_value=movies,
        ):
            result = select_eligible_candidates(
                object(),
                candidates=candidates,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
                movie_identity_supported=lambda movie_id: movie_id == 6,
                limit=100,
            )

        self.assertEqual([item.movie_id for item in result.candidates], [2, 3, 6])
        self.assertEqual(
            dict(result.diagnostics.rejection_counts),
            {HardFilterReason.UNTRUSTED_MATURE_COLD_ITEM.value: 3},
        )

    def test_long_term_ontology_candidate_cannot_bypass_cold_item_trust(self) -> None:
        profile = clean_profile()
        ontology_candidate = MergedCandidate(
            movie_id=1,
            sources=(CandidateSource.LONG_TERM_ONTOLOGY,),
            selection_rank=1,
            candidate_selection_score=1.0,
            long_term_ontology_raw_score=1.0,
            normalized_long_term_ontology_score=1.0,
            long_term_ontology_source_rank=1,
        )
        old_movie = movie(1)
        old_movie.release_date = AS_OF.date() - timedelta(days=1_000)
        old_movie.vote_count = 0

        with patch(
            "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
            return_value=[old_movie],
        ):
            result = select_eligible_candidates(
                object(),
                candidates=(ontology_candidate,),
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
                movie_identity_supported=lambda _movie_id: False,
                limit=100,
            )

        self.assertEqual(result.candidates, ())
        self.assertEqual(
            result.rejections[0].reasons,
            (HardFilterReason.UNTRUSTED_MATURE_COLD_ITEM,),
        )

    def test_cold_item_rejection_is_filled_from_reserve(self) -> None:
        profile = clean_profile()
        candidates = tuple(candidate(movie_id, movie_id) for movie_id in range(1, 102))
        movies = [movie(movie_id) for movie_id in range(1, 102)]
        movies[0].release_date = AS_OF.date() - timedelta(days=1_000)
        movies[0].vote_count = 0

        with patch(
            "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
            return_value=movies,
        ):
            result = select_eligible_candidates(
                object(),
                candidates=candidates,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
                movie_identity_supported=lambda movie_id: movie_id != 1,
                limit=100,
            )

        self.assertEqual(len(result.candidates), 100)
        self.assertEqual(result.candidates[-1].movie_id, 101)
        self.assertEqual(result.diagnostics.reserve_selected_count, 1)

    def test_rejected_active_candidates_are_filled_from_the_next_50(self) -> None:
        profile = clean_profile()
        candidates = tuple(candidate(movie_id, movie_id) for movie_id in range(1, 151))
        movies = [movie(movie_id) for movie_id in range(1, 151)]
        movies[9].adult = True
        movies[19].status = "취소됨"

        with patch(
            "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
            return_value=movies,
        ):
            result = select_eligible_candidates(
                object(),
                candidates=candidates,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
            )

        self.assertEqual(len(result.candidates), 100)
        self.assertNotIn(10, {item.movie_id for item in result.candidates})
        self.assertNotIn(20, {item.movie_id for item in result.candidates})
        self.assertEqual([item.movie_id for item in result.candidates[-2:]], [101, 102])
        self.assertEqual(result.diagnostics.reserve_selected_count, 2)
        self.assertEqual(result.diagnostics.inspected_candidate_count, 102)
        reasons = {item.movie_id: item.reasons for item in result.rejections}
        self.assertEqual(reasons[10], (HardFilterReason.ADULT,))
        self.assertEqual(reasons[20], (HardFilterReason.BLOCKED_STATUS,))

    def test_subscribed_only_uses_reserve_without_relaxing_ott_filter(self) -> None:
        profile = clean_profile()
        profile = replace(
            profile,
            serving_context=replace(
                profile.serving_context,
                ott_mode=OttFilterMode.SUBSCRIBED_ONLY,
                subscribed_ott_ids=frozenset({8}),
            ),
        )
        candidates = tuple(candidate(movie_id, movie_id) for movie_id in range(1, 151))
        movies = [movie(movie_id) for movie_id in range(1, 151)]
        available = set(range(51, 151))

        with (
            patch(
                "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
                return_value=movies,
            ),
            patch(
                "app.services.recsys.v3.retrieval.candidate_eligibility.load_streaming_movie_ids",
                return_value=available,
            ),
        ):
            result = select_eligible_candidates(
                object(),
                candidates=candidates,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
            )

        self.assertEqual([item.movie_id for item in result.candidates], list(range(51, 151)))
        self.assertEqual(result.diagnostics.reserve_selected_count, 50)
        self.assertEqual(result.diagnostics.rejected_candidate_count, 50)
        self.assertEqual(
            dict(result.diagnostics.rejection_counts),
            {HardFilterReason.NOT_ON_SUBSCRIBED_OTT.value: 50},
        )

    def test_returns_less_than_100_when_the_50_reserve_is_exhausted(self) -> None:
        profile = clean_profile()
        candidates = tuple(candidate(movie_id, movie_id) for movie_id in range(1, 151))
        movies = [movie(movie_id) for movie_id in range(1, 81)]

        with patch(
            "app.services.recsys.v3.retrieval.candidate_eligibility.load_movies_by_ids",
            return_value=movies,
        ):
            result = select_eligible_candidates(
                object(),
                candidates=candidates,
                profile=profile,
                context=PolicyRequestContext(as_of=AS_OF, limit=100),
            )

        self.assertEqual(len(result.candidates), 80)
        self.assertEqual(result.diagnostics.inspected_candidate_count, 150)
        self.assertEqual(result.diagnostics.rejected_candidate_count, 70)
        self.assertEqual(result.diagnostics.reserve_selected_count, 0)


if __name__ == "__main__":
    unittest.main()
