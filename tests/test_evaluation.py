import unittest
from pathlib import Path

from evaluation.benchmark import evaluate_ranking, load_cohorts
from evaluation.contracts import Interaction, RecommendationInput


class FixedCohortTest(unittest.TestCase):
    def test_all_required_cohorts_have_exact_unique_counts(self) -> None:
        cohorts = load_cohorts()
        self.assertEqual(list(cohorts), ["10", "50", "100", "150", "200", "500"])
        for name, user_ids in cohorts.items():
            self.assertEqual(len(user_ids), int(name))
            self.assertEqual(len(set(user_ids)), int(name))

    def test_known_historic_users_are_preserved(self) -> None:
        cohorts = load_cohorts()
        self.assertEqual(
            cohorts["10"],
            [35015, 47486, 52381, 151814, 157086, 160425, 161851, 191004, 196909, 198767],
        )


class MetricTest(unittest.TestCase):
    def test_ideal_ranking_scores_one(self) -> None:
        truth = {11: 5.0, 12: 4.5, 13: 3.0, 14: 1.0, 15: 2.0}
        result = evaluate_ranking([11, 12, 13, 14, 15], truth)
        self.assertEqual(result["k_at_20_percent"], 1)
        self.assertAlmostEqual(result["coverage"], 1.0)
        self.assertAlmostEqual(result["ndcg_at_20_percent"], 1.0)

    def test_unknown_and_duplicate_items_do_not_inflate_coverage(self) -> None:
        truth = {11: 5.0, 12: 4.0, 13: 1.0, 14: 2.0, 15: 3.0}
        result = evaluate_ranking([999, 11, 11], truth)
        self.assertAlmostEqual(result["coverage"], 0.2)
        self.assertEqual(result["returned_candidate_count"], 1)


class ContractTest(unittest.TestCase):
    def test_recommendation_input_contains_no_ground_truth(self) -> None:
        input_data = RecommendationInput(
            user_id=1,
            training_interactions=(Interaction(movie_id=10, rating=5.0, timestamp=100),),
            candidate_movie_ids=(20, 30),
        )
        self.assertFalse(hasattr(input_data, "ground_truth"))
        self.assertEqual(input_data.candidate_movie_ids, (20, 30))


if __name__ == "__main__":
    unittest.main()

