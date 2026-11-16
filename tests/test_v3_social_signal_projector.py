from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime

from app.jobs.recsys.v3.datasets.dataset_schemas import SocialSignalAction
from app.jobs.recsys.v3.datasets.social_signal_projector import (
    DIRECTION_UNRESOLVED,
    build_diagnostics,
    project_playlist_rows,
)


class SocialSignalProjectorTest(unittest.TestCase):
    def test_playlist_projection_conserves_each_event_unit(self) -> None:
        occurred_at = datetime(2026, 8, 20)
        rows = [
            (10, 1, 10, 100, 7, occurred_at),
            (10, 1, 10, 100, 8, occurred_at),
            (10, 1, 10, 100, 8, occurred_at),
            (11, 2, 11, 101, 9, occurred_at),
        ]

        signals, event_count = project_playlist_rows(
            rows,
            action=SocialSignalAction.PLAYLIST_POST_WRITE,
        )

        self.assertEqual(event_count, 2)
        self.assertEqual(len(signals), 3)
        unit_totals: dict[int, float] = {}
        for signal in signals:
            unit_totals[signal.source_id] = (
                unit_totals.get(signal.source_id, 0.0) + signal.distributed_unit
            )
        self.assertEqual(unit_totals, {10: 1.0, 11: 1.0})

    def test_projected_signals_are_diagnostic_only(self) -> None:
        signals, _event_count = project_playlist_rows(
            [(10, 1, 10, 100, 7, datetime(2026, 8, 20))],
            action=SocialSignalAction.PLAYLIST_POST_REPLY,
        )

        self.assertFalse(signals[0].eligible_for_training)
        self.assertEqual(signals[0].eligibility_reason, DIRECTION_UNRESOLVED)

    def test_diagnostics_separate_deferred_events(self) -> None:
        occurred_at = datetime(2026, 8, 20)
        signals, _event_count = project_playlist_rows(
            [(10, 1, 10, 100, 7, occurred_at)],
            action=SocialSignalAction.PLAYLIST_POST_WRITE,
        )

        diagnostics = build_diagnostics(
            tuple(signals),
            event_counts=Counter({SocialSignalAction.PLAYLIST_POST_WRITE.value: 1}),
            deferred_event_counts=Counter({SocialSignalAction.PLAYLIST_POST_LIKE.value: 2}),
            data_cutoff_at=occurred_at,
        )

        self.assertEqual(diagnostics.raw_signal_count, 1)
        self.assertEqual(diagnostics.eligible_signal_count, 0)
        self.assertEqual(diagnostics.action_unit_totals, {"playlist_post_write": 1.0})
        self.assertEqual(diagnostics.deferred_event_counts, {"playlist_post_like": 2})


if __name__ == "__main__":
    unittest.main()
