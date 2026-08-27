import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from evaluation.benchmark import evaluate_ranking, load_cohorts
from evaluation.contracts import Interaction, RecommendationInput
from evaluation.datasets import resolve_dataset, resolve_movie_identities
from evaluation.engine import get_evaluation_engine
from app.services.recsys.registry import (
    UnsupportedRecommendationEngineError,
    get_recommendation_adapter,
)


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

    def test_evaluation_engine_delegates_to_the_selected_app_adapter(self) -> None:
        evaluator = object()

        class Adapter:
            def create_evaluation_engine(self):
                return evaluator

        with patch("evaluation.engine.get_recommendation_adapter", return_value=Adapter()):
            self.assertIs(get_evaluation_engine("v2"), evaluator)

    def test_dataset_versions_use_isolated_paths(self) -> None:
        fixed_v1 = resolve_dataset("fixed-v1")
        fixed_v2 = resolve_dataset("fixed-v2")
        self.assertNotEqual(fixed_v1.cases, fixed_v2.cases)
        self.assertNotEqual(fixed_v1.movie_identities, fixed_v2.movie_identities)
        self.assertEqual(fixed_v2.cases.name, "cases.jsonl.gz")
        self.assertEqual(fixed_v2.cases.parent.name, "fixed-v2")

    def test_movie_identity_resolution_remaps_changed_internal_ids(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "movie_identities.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as output:
                json.dump(
                    {
                        "schema_version": 1,
                        "identity": "tmdb_id",
                        "movies": [[10, 101], [20, 202]],
                    },
                    output,
                )
            with patch(
                "evaluation.datasets.load_current_movie_ids_by_tmdb",
                return_value={101: 9001, 202: 9002},
            ):
                resolution = resolve_movie_identities(path, {10, 20})
        self.assertEqual(resolution.movie_id_map, {10: 9001, 20: 9002})
        self.assertEqual(resolution.metadata["remapped_movie_count"], 2)

    def test_movie_identity_resolution_rejects_movies_missing_from_db(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "movie_identities.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as output:
                json.dump(
                    {
                        "schema_version": 1,
                        "identity": "tmdb_id",
                        "movies": [[10, 101], [20, 202]],
                    },
                    output,
                )
            with patch(
                "evaluation.datasets.load_current_movie_ids_by_tmdb",
                return_value={101: 9001},
            ):
                with self.assertRaisesRegex(ValueError, "TMDB movies are missing"):
                    resolve_movie_identities(path, {10, 20})

    def test_registry_discovers_versioned_adapters_by_convention(self) -> None:
        self.assertEqual(get_recommendation_adapter("v1").name, "v1")
        self.assertEqual(get_recommendation_adapter("v2").name, "v2")
        with self.assertRaises(UnsupportedRecommendationEngineError):
            get_recommendation_adapter("v999")


if __name__ == "__main__":
    unittest.main()

