from __future__ import annotations

import unittest

import numpy as np
from scipy.sparse import coo_matrix

from tests.v3_long_term_eval.run_lightfm_factorial import (
    anchor_check,
    comparison_variants,
    factorial_variants,
    factor_effect,
    interaction_effect,
    supported_item_identity,
    three_factor_interaction,
    variant_summary,
)


class LightFMFactorialTest(unittest.TestCase):
    def test_builds_all_eight_unique_variants_and_both_anchors(self) -> None:
        variants = factorial_variants()

        self.assertEqual(len(variants), 8)
        self.assertEqual(len({variant.name for variant in variants}), 8)
        self.assertIn(
            "signal-baseline__identity-implicit__config-baseline",
            {variant.name for variant in variants},
        )
        self.assertIn(
            "signal-v3__identity-supported__config-v3",
            {variant.name for variant in variants},
        )

    def test_supported_item_identity_uses_every_observed_column(self) -> None:
        interactions = coo_matrix(
            (
                np.asarray((1.0, -1.0, 1.0), dtype=np.float32),
                (
                    np.asarray((0, 0, 1), dtype=np.int32),
                    np.asarray((1, 3, 1), dtype=np.int32),
                ),
            ),
            shape=(2, 5),
        )

        result = supported_item_identity(interactions, movie_count=5).toarray()

        expected = np.zeros((5, 5), dtype=np.float32)
        expected[1, 1] = 1.0
        expected[3, 3] = 1.0
        np.testing.assert_array_equal(result, expected)

    def test_final_comparison_uses_only_locked_endpoints(self) -> None:
        variants = comparison_variants()

        self.assertEqual(len(variants), 2)
        self.assertEqual(
            (variants[0].signal, variants[0].identity, variants[0].config),
            ("baseline", "implicit", "baseline"),
        )
        self.assertEqual(
            (variants[1].signal, variants[1].identity, variants[1].config),
            ("v3", "supported", "v3"),
        )

    def test_main_effects_and_interactions_are_paired(self) -> None:
        rows = synthetic_rows()

        signal = factor_effect(rows, factor="signal")
        identity = factor_effect(rows, factor="identity")
        config = factor_effect(rows, factor="config")
        signal_identity = interaction_effect(rows, left="signal", right="identity")
        three_factor = three_factor_interaction(rows)

        self.assertAlmostEqual(
            signal["mean_delta_ndcg_at_100_percent"], 0.1425, places=12
        )
        self.assertAlmostEqual(
            identity["mean_delta_ndcg_at_100_percent"], -0.1575, places=12
        )
        self.assertAlmostEqual(
            config["mean_delta_ndcg_at_100_percent"], 0.3175, places=12
        )
        self.assertAlmostEqual(
            signal_identity["mean_delta_ndcg_at_100_percent"], 0.085, places=12
        )
        self.assertAlmostEqual(
            three_factor["mean_delta_ndcg_at_100_percent"], 0.07, places=12
        )

    def test_anchor_check_requires_both_reference_values(self) -> None:
        variants = factorial_variants()
        rows = []
        timings = {}
        for variant in variants:
            value = 0.5
            if variant.name.endswith("identity-implicit__config-baseline"):
                value = 0.873887
            if variant.name == "signal-v3__identity-supported__config-v3":
                value = 0.879018
            rows.append(
                {
                    "variant": variant.name,
                    "ndcg_at_100_percent": value,
                    "ndcg_at_20_percent": value,
                }
            )
            timings[variant.name] = [1.0]
        summaries = [
            variant_summary(variant, rows, timings[variant.name])
            for variant in variants
        ]

        self.assertTrue(anchor_check(summaries)["all_reproduced"])

    def test_anchor_check_can_be_disabled_for_a_new_cohort(self) -> None:
        result = anchor_check([], expected_anchors=None)

        self.assertFalse(result["required"])
        self.assertIsNone(result["all_reproduced"])
        self.assertEqual(result["variants"], {})


def synthetic_rows() -> list[dict]:
    rows = []
    for variant in factorial_variants():
        signal = 1.0 if variant.signal == "v3" else 0.0
        identity = 1.0 if variant.identity == "supported" else 0.0
        config = 1.0 if variant.config == "v3" else 0.0
        score = (
            0.5
            + 0.1 * signal
            - 0.2 * identity
            + 0.3 * config
            + 0.05 * signal * identity
            + 0.07 * signal * identity * config
        )
        for seed in (42, 43, 44):
            for source_user_id, profile_type in (
                (1, "stable_focused"),
                (2, "stable_diverse"),
            ):
                rows.append(
                    {
                        "variant": variant.name,
                        "signal": variant.signal,
                        "identity": variant.identity,
                        "config": variant.config,
                        "seed": seed,
                        "source_user_id": source_user_id,
                        "profile_type": profile_type,
                        "ndcg_at_100_percent": score,
                    }
                )
    return rows


if __name__ == "__main__":
    unittest.main()
