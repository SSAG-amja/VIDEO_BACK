from __future__ import annotations

import unittest

from app.services.recsys.profile_change import PendingShortTermRefresh
from app.services.recsys.v3.retrieval.short_term_refresh_policy import evaluate_short_term_refresh


def pending(
    weights: tuple[float, ...],
    *,
    last_change_at: float = 100.0,
    eligible_at: float | None = None,
    force_refresh: bool = False,
) -> PendingShortTermRefresh:
    return PendingShortTermRefresh(
        user_id=7,
        revision=3,
        positive_movie_weights=tuple(
            (index, weight) for index, weight in enumerate(weights, start=1)
        ),
        first_positive_at=50.0 if weights else None,
        last_change_at=last_change_at,
        eligible_at=eligible_at,
        force_refresh=force_refresh,
    )


class ShortTermRefreshPolicyTest(unittest.TestCase):
    def test_one_positive_action_is_collected_without_refresh(self) -> None:
        decision = evaluate_short_term_refresh(pending((1.0,)), now=100.0)

        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "insufficient_positive_evidence")

    def test_two_strong_or_three_watched_actions_reach_threshold(self) -> None:
        two_saved = evaluate_short_term_refresh(pending((1.0, 1.0)), now=100.0)
        three_watched = evaluate_short_term_refresh(pending((0.75, 0.75, 0.75)), now=100.0)

        self.assertTrue(two_saved.eligible)
        self.assertTrue(three_watched.eligible)
        self.assertFalse(two_saved.ready)

    def test_debounce_waits_30_seconds_after_last_change(self) -> None:
        state = pending((1.0, 1.0), last_change_at=100.0, eligible_at=100.0)

        self.assertFalse(evaluate_short_term_refresh(state, now=129.9).ready)
        self.assertTrue(evaluate_short_term_refresh(state, now=130.0).ready)

    def test_max_wait_caps_continuous_behavior_at_two_minutes(self) -> None:
        state = pending((1.0, 1.0, 1.0), last_change_at=215.0, eligible_at=100.0)
        decision = evaluate_short_term_refresh(state, now=220.0)

        self.assertTrue(decision.ready)
        self.assertEqual(decision.due_at, 220.0)

    def test_positive_removal_forces_a_debounced_refresh(self) -> None:
        state = pending((), last_change_at=100.0, eligible_at=100.0, force_refresh=True)

        decision = evaluate_short_term_refresh(state, now=130.0)
        self.assertTrue(decision.ready)
        self.assertEqual(decision.reason, "forced_positive_removal")


if __name__ == "__main__":
    unittest.main()
