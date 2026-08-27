from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from app.services.recsys.v3.retrieval.candidate_eligibility import select_eligible_candidates
from app.services.recsys.v3.policy.policy_schemas import HardFilterReason, PolicyRequestContext
from app.services.recsys.v3.retrieval.retrieval_schemas import CandidateSource, MergedCandidate
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
