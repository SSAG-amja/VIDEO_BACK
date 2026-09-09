from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix

from app.jobs.recsys.v3.datasets.dataset_schemas import (
    DatasetDiagnostics,
    LightFMDatasetSnapshot,
    PositiveInteraction,
)
from app.services.recsys.v3.domain.behavior import SnapshotAction
from tests.v3_long_term_eval.evaluation_dataset import restrict_to_evaluation_users
from tests.v3_long_term_eval.evaluate_pure_lightfm import (
    build_training_matrices,
    lightfm_signal,
)

from tests.v3_long_term_eval.metrics import (
    evaluation_cutoffs,
    ndcg_at_k,
    rating_relevance,
)
from tests.v3_long_term_eval.movielens import (
    PROFILE_TYPES,
    MappedRating,
    SelectionThresholds,
    UserSummary,
    classify_user,
    iter_mapped_user_ratings,
    normalized_genre_entropy,
    select_balanced_user_ids,
    select_balanced_user_sequence,
    select_focused_user_sequence,
    temporal_split,
)
from tests.v3_long_term_eval.prepare import parse_user_counts


def rating(movie_id: int, value: float, timestamp: int) -> MappedRating:
    return MappedRating(
        source_movie_id=movie_id,
        movie_id=movie_id + 1_000,
        rating=value,
        timestamp=timestamp,
    )


class LongTermEvaluationHelperTests(unittest.TestCase):
    def test_rating_relevance_preserves_positive_grade_order(self) -> None:
        self.assertEqual(
            [rating_relevance(value) for value in (3.0, 3.5, 4.0, 4.5, 5.0)],
            [0.0, 1.0, 2.0, 3.0, 4.0],
        )

    def test_ndcg_rewards_correct_order(self) -> None:
        relevance = {11: 4.0, 12: 2.0, 13: 1.0}
        self.assertAlmostEqual(ndcg_at_k([11, 12, 13, 99], relevance, 3), 1.0)
        self.assertLess(ndcg_at_k([13, 12, 11, 99], relevance, 3), 1.0)

    def test_metrics_reject_duplicate_ranked_movies(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            ndcg_at_k([11, 11], {11: 1.0}, 2)

    def test_temporal_split_keeps_oldest_seventy_percent_for_training(self) -> None:
        ratings = tuple(rating(index, 4.0, index) for index in range(10))
        train, holdout = temporal_split(ratings)
        self.assertEqual(len(train), 7)
        self.assertEqual(len(holdout), 3)
        self.assertLess(train[-1].timestamp, holdout[0].timestamp)

    def test_movie_mapping_keeps_latest_rating_per_database_movie(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ratings.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("userId", "movieId", "rating", "timestamp"))
                writer.writerow((1, 10, 3.5, 100))
                writer.writerow((1, 11, 5.0, 200))
            rows = list(
                iter_mapped_user_ratings(
                    path,
                    source_to_db_movie_id={10: 99, 11: 99},
                )
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0][1]), 1)
        self.assertEqual(rows[0][1][0].source_movie_id, 11)
        self.assertEqual(rows[0][1][0].rating, 5.0)

    def test_genre_entropy_distinguishes_focused_and_diverse_histories(self) -> None:
        focused = tuple(rating(index, 4.0, index) for index in range(4))
        diverse = tuple(rating(index + 10, 4.0, index) for index in range(4))
        genres = {
            **{item.source_movie_id: ("Drama",) for item in focused},
            **{
                item.source_movie_id: (genre,)
                for item, genre in zip(
                    diverse,
                    ("Drama", "Action", "Comedy", "Horror"),
                    strict=True,
                )
            },
        }
        self.assertEqual(
            normalized_genre_entropy(focused, genres_by_source_movie_id=genres),
            0.0,
        )
        self.assertAlmostEqual(
            normalized_genre_entropy(diverse, genres_by_source_movie_id=genres),
            1.0,
        )

    def test_balanced_selection_returns_each_profile_type(self) -> None:
        thresholds = SelectionThresholds(activity_median=100, genre_entropy_median=0.5)
        summaries = [
            UserSummary(1, 80, 56, 30, 10, 0.2),
            UserSummary(2, 90, 63, 30, 10, 0.8),
            UserSummary(3, 120, 84, 30, 10, 0.2),
            UserSummary(4, 130, 91, 30, 10, 0.8),
        ]
        selected = select_balanced_user_ids(
            summaries,
            thresholds=thresholds,
            users_per_type=1,
            seed=7,
        )
        self.assertEqual(
            {classify_user(item, thresholds) for item in summaries},
            set(selected.values()),
        )
        self.assertEqual(len(selected), 4)

    def test_balanced_sequence_is_nested_and_nearly_even(self) -> None:
        thresholds = SelectionThresholds(activity_median=100, genre_entropy_median=0.5)
        summaries = []
        source_user_id = 1
        for mapped_count, entropy in ((80, 0.2), (80, 0.8), (120, 0.2), (120, 0.8)):
            for _ in range(4):
                summaries.append(
                    UserSummary(source_user_id, mapped_count, 70, 30, 10, entropy)
                )
                source_user_id += 1

        sequence = select_balanced_user_sequence(
            summaries,
            thresholds=thresholds,
            user_count=10,
            seed=7,
        )

        self.assertEqual(len(sequence), 10)
        self.assertEqual(len({source_user_id for source_user_id, _ in sequence}), 10)
        self.assertEqual(
            [profile_type for _, profile_type in sequence[:4]],
            list(PROFILE_TYPES),
        )
        counts = Counter(profile_type for _, profile_type in sequence)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_focused_sequence_excludes_diverse_users_and_spreads_genres(self) -> None:
        thresholds = SelectionThresholds(activity_median=100, genre_entropy_median=0.5)
        summaries = [
            UserSummary(1, 100, 70, 30, 10, 0.2, "Action"),
            UserSummary(2, 100, 70, 30, 10, 0.3, "Drama"),
            UserSummary(3, 100, 70, 30, 10, 0.1, "Action"),
            UserSummary(4, 100, 70, 30, 10, 0.2, "Drama"),
            UserSummary(5, 100, 70, 30, 10, 0.8, "Comedy"),
        ]

        sequence = select_focused_user_sequence(
            summaries,
            thresholds=thresholds,
            user_count=4,
            seed=7,
        )

        selected_ids = {source_user_id for source_user_id, _ in sequence}
        self.assertNotIn(5, selected_ids)
        self.assertEqual({profile_type for _, profile_type in sequence}, {"focused"})
        self.assertEqual(
            [summaries[source_user_id - 1].dominant_genre for source_user_id, _ in sequence[:2]],
            ["Action", "Drama"],
        )

    def test_user_counts_are_unique_ascending_positive_values(self) -> None:
        self.assertEqual(parse_user_counts("100,150,200,300,500"), (100, 150, 200, 300, 500))
        for invalid in ("", "100,0", "150,100", "100,100"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_user_counts(invalid)

    def test_pure_lightfm_signal_mapping(self) -> None:
        self.assertEqual(lightfm_signal(1.0), (-1.0, 1.0, "passed"))
        self.assertIsNone(lightfm_signal(2.5))
        self.assertEqual(lightfm_signal(4.0), (1.0, 1.0, "pinned"))
        self.assertEqual(lightfm_signal(5.0), (1.0, 1.5, "saved"))

    def test_pure_lightfm_training_matrices_keep_labels_and_confidence(self) -> None:
        plans = [
            {
                "train": [
                    {"movie_id": 10, "rating": 1.0},
                    {"movie_id": 20, "rating": 2.5},
                    {"movie_id": 30, "rating": 4.0},
                    {"movie_id": 40, "rating": 5.0},
                ]
            }
        ]
        interactions, weights, counts = build_training_matrices(
            plans,
            movie_id_map={10: 0, 20: 1, 30: 2, 40: 3},
        )

        self.assertEqual(interactions.data.tolist(), [-1.0, 1.0, 1.0])
        self.assertEqual(weights.data.tolist(), [1.0, 1.0, 1.5])
        self.assertEqual(
            counts,
            {"passed": 1, "pinned": 1, "saved": 1, "neutral_excluded": 1},
        )

    def test_evaluation_cutoffs_use_tens_and_include_candidate_count(self) -> None:
        self.assertEqual(evaluation_cutoffs(30), (10, 20, 30))
        self.assertEqual(evaluation_cutoffs(55), (10, 20, 30, 40, 50, 55))
        self.assertEqual(evaluation_cutoffs(7), (7,))

    def test_shadow_dataset_keeps_only_evaluation_users(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        positives = (
            PositiveInteraction(
                1,
                10,
                (SnapshotAction.PINNED,),
                SnapshotAction.PINNED,
                1.0,
                cutoff,
            ),
            PositiveInteraction(
                2,
                20,
                (SnapshotAction.SAVED,),
                SnapshotAction.SAVED,
                1.0,
                cutoff,
            ),
        )
        interactions = coo_matrix(np.eye(2, dtype=np.float32))
        diagnostics = DatasetDiagnostics(
            data_cutoff_at=cutoff,
            catalog_movie_count=2,
            model_user_count=2,
            positive_pair_count=2,
            raw_signal_count=2,
            action_signal_counts={"pinned": 1, "saved": 1},
            passed_movie_count=0,
            watched_movie_count=0,
            excluded_pair_count=0,
            passed_positive_conflict_count=0,
            missing_timestamp_count=0,
            dataset_hash="full",
        )
        dataset = LightFMDatasetSnapshot(
            data_cutoff_at=cutoff,
            user_ids=(1, 2),
            movie_ids=(10, 20),
            user_id_map={1: 0, 2: 1},
            movie_id_map={10: 0, 20: 1},
            interactions=interactions,
            sample_weights=interactions.copy(),
            positives=positives,
            diagnostics=diagnostics,
        )

        selected = restrict_to_evaluation_users(dataset, user_ids=(2,))

        self.assertEqual(selected.user_ids, (2,))
        self.assertEqual(selected.user_id_map, {2: 0})
        self.assertEqual(selected.interactions.shape, (1, 2))
        self.assertEqual(selected.interactions.nnz, 1)
        self.assertEqual([item.user_id for item in selected.positives], [2])
        self.assertEqual(selected.diagnostics.model_user_count, 1)

if __name__ == "__main__":
    unittest.main()
