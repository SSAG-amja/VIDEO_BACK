from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from app.services.recsys.v3.domain.behavior import SnapshotAction
from tests.v3_long_term_eval.run_ablation_experiment import (
    TrainingSignals,
    corrected_signals,
    effect_verdict,
    signal_for_rating,
    signals_for_loss,
)
from scipy.sparse import coo_matrix


class V3LongTermAblationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.occurred_at = datetime(2025, 11, 2, tzinfo=timezone.utc)

    def test_baseline_rating_mapping(self) -> None:
        self.assertEqual(
            signal_for_rating(
                1.0,
                occurred_at=self.occurred_at,
                as_of=self.as_of,
                policy="baseline",
            ),
            (-1.0, 1.0, SnapshotAction.PASSED),
        )
        self.assertEqual(
            signal_for_rating(
                4.0,
                occurred_at=self.occurred_at,
                as_of=self.as_of,
                policy="baseline",
            ),
            (1.0, 1.0, SnapshotAction.PINNED),
        )
        self.assertEqual(
            signal_for_rating(
                5.0,
                occurred_at=self.occurred_at,
                as_of=self.as_of,
                policy="baseline",
            ),
            (1.0, 1.5, SnapshotAction.SAVED),
        )
        self.assertIsNone(
            signal_for_rating(
                2.5,
                occurred_at=self.occurred_at,
                as_of=self.as_of,
                policy="baseline",
            )
        )

    def test_v3_decay_uses_action_half_life(self) -> None:
        label, weight, action = signal_for_rating(
            5.0,
            occurred_at=self.occurred_at,
            as_of=self.as_of,
            policy="v3_decay",
        )
        self.assertEqual(label, 1.0)
        self.assertEqual(action, SnapshotAction.SAVED)
        self.assertAlmostEqual(weight, 1.0, places=6)

    def test_corrections_do_not_change_interaction_labels(self) -> None:
        coordinates = (np.asarray([0, 0, 1]), np.asarray([0, 1, 0]))
        signals = TrainingSignals(
            interactions=coo_matrix(
                (np.asarray([1.0, -1.0, 1.0]), coordinates),
                shape=(2, 2),
            ),
            sample_weights=coo_matrix(
                (np.asarray([2.0, 1.0, 1.0]), coordinates),
                shape=(2, 2),
            ),
            positives=(),
        )
        corrected = corrected_signals(
            signals,
            item_frequency_weighting=True,
            user_activity_weighting=True,
        )
        np.testing.assert_array_equal(
            corrected.interactions.data,
            signals.interactions.data,
        )
        self.assertTrue(np.all(corrected.sample_weights.data > 0))

    def test_effect_verdict(self) -> None:
        self.assertEqual(effect_verdict(0.01, 0.01), "favorable")
        self.assertEqual(effect_verdict(-0.01, -0.01), "unfavorable")
        self.assertEqual(effect_verdict(0.002, -0.002), "neutral")

    def test_warp_excludes_explicit_pass_rows(self) -> None:
        coordinates = (np.asarray([0, 0, 1]), np.asarray([0, 1, 0]))
        signals = TrainingSignals(
            interactions=coo_matrix(
                (np.asarray([1.0, -1.0, 1.0]), coordinates),
                shape=(2, 2),
            ),
            sample_weights=coo_matrix(
                (np.asarray([2.0, 1.0, 1.0]), coordinates),
                shape=(2, 2),
            ),
            positives=(),
        )
        warp = signals_for_loss(signals, loss="warp")
        np.testing.assert_array_equal(warp.interactions.data, np.asarray([1.0, 1.0]))
        np.testing.assert_array_equal(warp.sample_weights.data, np.asarray([2.0, 1.0]))


if __name__ == "__main__":
    unittest.main()
