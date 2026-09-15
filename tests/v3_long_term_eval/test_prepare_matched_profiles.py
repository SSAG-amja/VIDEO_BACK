from __future__ import annotations

import argparse
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.v3_long_term_eval.movielens import UserSummary
from tests.v3_long_term_eval.prepare_continuous_entropy_profiles import (
    activity_genre_matched_triplets,
    triplet_relative_activity_gap,
)
from tests.v3_long_term_eval.prepare_matched_profiles import (
    load_excluded_source_user_ids,
    parse_datetime,
    resolve_evaluation_as_of,
)
from tests.v3_long_term_eval.prepare_recency_profiles import ProfileCandidate


class PrepareMatchedProfilesTest(unittest.TestCase):
    def test_matches_continuous_entropy_triplets_by_genre_and_activity(self) -> None:
        def candidate(user_id: int, count: int, entropy: float) -> ProfileCandidate:
            summary = UserSummary(
                source_user_id=user_id,
                mapped_rating_count=count,
                train_rating_count=70,
                train_positive_count=30,
                holdout_positive_count=10,
                genre_entropy=entropy,
                dominant_genre="Drama",
                temporal_genre_shift=0.1,
            )
            return ProfileCandidate(summary=summary, temporal_genre_shift=0.1)

        triplets = activity_genre_matched_triplets(
            [candidate(1, 100, 0.2), candidate(2, 200, 0.3)],
            [candidate(3, 105, 0.5), candidate(4, 190, 0.6)],
            [candidate(5, 110, 0.8), candidate(6, 180, 0.9)],
            count=2,
            seed=42,
            maximum_relative_activity_gap=0.20,
        )

        self.assertEqual(len(triplets), 2)
        self.assertTrue(
            all(
                len({item.summary.dominant_genre for item in triplet}) == 1
                for triplet in triplets
            )
        )
        self.assertTrue(
            all(triplet_relative_activity_gap(triplet) <= 0.20 for triplet in triplets)
        )

    def test_loads_unique_excluded_source_users(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.jsonl"
            path.write_text(
                '{"source_user_id": 10}\n{"source_user_id": 20}\n'
                '{"source_user_id": 10}\n',
                encoding="utf-8",
            )

            result = load_excluded_source_user_ids([path])

        self.assertEqual(result, frozenset((10, 20)))

    def test_rejects_missing_exclusion_plan(self) -> None:
        with self.assertRaises(ValueError):
            load_excluded_source_user_ids([Path("missing-plan.jsonl")])

    def test_resolves_shared_evaluation_time_from_exclusion_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ("first.jsonl", "second.jsonl"):
                path = Path(directory) / name
                path.write_text(
                    '{"evaluation_as_of": "2026-09-10T13:04:41+00:00"}\n',
                    encoding="utf-8",
                )
                paths.append(path)

            result = resolve_evaluation_as_of(explicit=None, exclusion_plans=paths)

        self.assertEqual(result, datetime(2026, 9, 10, 13, 4, 41, tzinfo=timezone.utc))

    def test_explicit_evaluation_time_does_not_require_an_exclusion_plan(self) -> None:
        expected = datetime(2026, 9, 10, 13, 4, 41, tzinfo=timezone.utc)

        result = resolve_evaluation_as_of(explicit=expected, exclusion_plans=[])

        self.assertEqual(result, expected)

    def test_rejects_naive_evaluation_time(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_datetime("2026-09-10T13:04:41")


if __name__ == "__main__":
    unittest.main()
