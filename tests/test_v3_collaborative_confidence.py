from __future__ import annotations

import unittest

from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_population_collaborative_confidence,
    assess_user_collaborative_confidence,
)


class CollaborativeConfidenceTest(unittest.TestCase):
    def test_small_bootstrap_population_limits_confidence(self) -> None:
        assessment = assess_population_collaborative_confidence(
            user_count=128,
            max_item_user_support_ratio=0.20,
            candidate_pairwise_jaccard=0.20,
        )

        self.assertLess(assessment["confidence"], 0.2)

    def test_large_but_homogeneous_population_stays_untrusted(self) -> None:
        assessment = assess_population_collaborative_confidence(
            user_count=1_000,
            max_item_user_support_ratio=0.90,
            candidate_pairwise_jaccard=0.85,
        )

        self.assertEqual(assessment["population_size_confidence"], 1.0)
        self.assertEqual(assessment["confidence"], 0.0)

    def test_user_evidence_and_population_are_both_required(self) -> None:
        sparse = assess_user_collaborative_confidence(
            positive_pair_count=3,
            population_confidence=1.0,
        )
        immature_population = assess_user_collaborative_confidence(
            positive_pair_count=100,
            population_confidence=0.25,
        )
        mature = assess_user_collaborative_confidence(
            positive_pair_count=20,
            population_confidence=1.0,
        )

        self.assertEqual(sparse.effective_confidence, 0.0)
        self.assertEqual(immature_population.effective_confidence, 0.25)
        self.assertEqual(mature.effective_confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
