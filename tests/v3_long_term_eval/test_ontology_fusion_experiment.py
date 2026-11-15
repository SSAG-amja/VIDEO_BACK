from __future__ import annotations

import unittest

from app.services.recsys.v3.domain.behavior import SnapshotAction
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.domain.schemas import FeatureDirection, ProfileFeatureSignal
from tests.v3_long_term_eval.run_ontology_fusion_experiment import (
    FAMILY_ABLATION_VARIANTS,
    ONTOLOGY_FAMILIES,
    UserOntologyContext,
    average_ranks,
    build_concentration_diagnostic,
    filter_profile_features_by_evidence,
    grouped_delta_means,
    normalize_ontology_scores,
    ontology_scores_for_variant,
    paired_deltas,
    profile_action_for_rating,
    rank_correlation,
    rank_movies,
)
from tests.v3_long_term_eval.run_final_generalization import adapt_pure_rows


class OntologyFusionExperimentTest(unittest.TestCase):
    def test_adapt_pure_rows_uses_each_users_full_holdout_ndcg(self) -> None:
        rows = adapt_pure_rows(
            [
                {
                    "source_user_id": 7,
                    "candidate_count": 3,
                    "ndcg": {"3": 0.8},
                    "ndcg_at_20_percent": 0.6,
                }
            ],
            plans=[{"source_user_id": 7, "profile_type": "stable_mid_entropy"}],
            cohort_name="final_a",
            seed=42,
        )

        self.assertEqual(rows[0]["variant"], "pure_lightfm")
        self.assertEqual(rows[0]["profile_type"], "stable_mid_entropy")
        self.assertAlmostEqual(rows[0]["ndcg_at_100_percent"], 0.8)
        self.assertAlmostEqual(rows[0]["ndcg_at_20_percent"], 0.6)

    def test_profile_action_uses_only_positive_behavior_proxies(self) -> None:
        self.assertEqual(profile_action_for_rating(5.0), SnapshotAction.SAVED)
        self.assertEqual(profile_action_for_rating(4.0), SnapshotAction.PINNED)
        self.assertIsNone(profile_action_for_rating(3.0))
        self.assertIsNone(profile_action_for_rating(1.0))

    def test_rank_movies_is_score_descending_with_movie_id_tiebreak(self) -> None:
        self.assertEqual(
            rank_movies({9: 0.2, 3: 0.7, 2: 0.7}),
            [2, 3, 9],
        )

    def test_rank_correlation_handles_constant_ontology_scores(self) -> None:
        self.assertIsNone(
            rank_correlation(
                {1: 0.0, 2: 0.5, 3: 1.0},
                {1: 0.5, 2: 0.5, 3: 0.5},
            )
        )
        self.assertAlmostEqual(
            rank_correlation(
                {1: 0.0, 2: 0.5, 3: 1.0},
                {1: 1.0, 2: 0.5, 3: 0.0},
            ),
            -1.0,
        )

    def test_repeated_evidence_profile_drops_one_off_features(self) -> None:
        features = (
            ProfileFeatureSignal(
                feature=FeatureName.GENRE,
                ref_id="1",
                direction=FeatureDirection.POSITIVE,
                score=1.0,
                contribution_count=3,
            ),
            ProfileFeatureSignal(
                feature=FeatureName.ACTOR,
                ref_id="2",
                direction=FeatureDirection.POSITIVE,
                score=0.5,
                contribution_count=1,
            ),
        )
        self.assertEqual(
            filter_profile_features_by_evidence(
                features,
                minimum_contribution_count=2,
            ),
            (features[0],),
        )

    def test_bounded_raw_normalization_preserves_absolute_scores(self) -> None:
        self.assertEqual(
            normalize_ontology_scores(
                {1: 0.1, 2: 0.4},
                method="bounded_raw",
            ),
            {1: 0.1, 2: 0.4},
        )
        with self.assertRaises(ValueError):
            normalize_ontology_scores({1: 1.1}, method="bounded_raw")

    def test_family_ablation_subtracts_without_redistributing_budget(self) -> None:
        scores_by_family = {
            family: {1: 0.0, 2: 0.0} for family in ONTOLOGY_FAMILIES
        }
        scores_by_family["genre"] = {1: 0.2, 2: 0.1}
        scores_by_family["keyword"] = {1: 0.3, 2: 0.0}
        context = UserOntologyContext(
            source_user_id=7,
            scores_by_movie={1: 0.5, 2: 0.1},
            profile_feature_count=2,
            positive_movie_count=1,
            graph_covered_positive_movie_count=1,
            matched_candidate_count=2,
            family_score_totals={"genre": 0.3, "keyword": 0.3},
            scores_by_family=scores_by_family,
        )

        self.assertEqual(
            ontology_scores_for_variant(
                context,
                excluded_families=("genre",),
            ),
            {1: 0.3, 2: 0.0},
        )
        with self.assertRaises(ValueError):
            ontology_scores_for_variant(
                context,
                excluded_families=("unknown",),
            )

    def test_family_ablation_grid_excludes_each_family_once(self) -> None:
        exclusions = [
            variant.excluded_families
            for variant in FAMILY_ABLATION_VARIANTS
            if variant.excluded_families
        ]
        self.assertEqual(len(FAMILY_ABLATION_VARIANTS), 8)
        self.assertEqual(exclusions, [(family,) for family in ONTOLOGY_FAMILIES])

    def test_concentration_diagnostic_uses_train_summary_and_finds_direction(self) -> None:
        effects = (0.04, 0.02, -0.01, -0.03)
        entropies = (0.1, 0.3, 0.7, 0.9)
        plans = []
        rows = []
        for user_id, (entropy, effect) in enumerate(
            zip(entropies, effects, strict=True), start=1
        ):
            profile_type = (
                "stable_low_entropy"
                if user_id == 1
                else "stable_mid_entropy"
                if user_id == 2
                else "stable_high_entropy"
            )
            plans.append(
                {
                    "source_user_id": user_id,
                    "profile_type": profile_type,
                    "train": [{"rating": 4.0}],
                    "summary": {
                        "genre_entropy": entropy,
                        "train_positive_count": 1,
                    },
                }
            )
            for seed in (42, 43):
                for variant, score in (
                    ("model_only", 0.5),
                    ("current_bounded_75_25", 0.5 + effect),
                ):
                    rows.append(
                        {
                            "cohort": "development",
                            "variant": variant,
                            "seed": seed,
                            "source_user_id": user_id,
                            "profile_type": profile_type,
                            "ndcg_at_100_percent": score,
                        }
                    )

        diagnostic = build_concentration_diagnostic(
            rows,
            cohorts={"development": plans},
        )

        self.assertTrue(diagnostic["directional_signal_found"])
        self.assertFalse(diagnostic["serving_threshold_selected"])
        self.assertTrue(diagnostic["sampling_design"]["continuous_low_middle_high"])
        self.assertIsNone(diagnostic["selection_gap"])
        for actual, expected in zip(
            [
                row["mean_delta_ndcg_at_100_percent"]
                for row in diagnostic["entropy_quartiles"]
            ],
            effects,
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)

    def test_average_ranks_assigns_ties_the_same_rank(self) -> None:
        self.assertEqual(average_ranks([3.0, 1.0, 1.0]), [2.0, 0.5, 0.5])

    def test_paired_delta_matches_same_cohort_seed_and_user(self) -> None:
        rows = [
            {
                "cohort": "a",
                "variant": "model_only",
                "seed": 42,
                "source_user_id": 7,
                "profile_type": "stable_focused",
                "score": 0.4,
            },
            {
                "cohort": "a",
                "variant": "current_75_25",
                "seed": 42,
                "source_user_id": 7,
                "profile_type": "stable_focused",
                "score": 0.5,
            },
        ]
        deltas = paired_deltas(rows, metric="score")
        self.assertEqual(len(deltas), 1)
        self.assertAlmostEqual(deltas[0]["delta"], 0.1)
        self.assertAlmostEqual(grouped_delta_means(deltas, "cohort")["a"], 0.1)


if __name__ == "__main__":
    unittest.main()
