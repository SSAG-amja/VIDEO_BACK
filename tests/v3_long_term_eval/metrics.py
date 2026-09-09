from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


DEFAULT_CUTOFFS = (10, 20, 50, 100)


def evaluation_cutoffs(candidate_count: int, *, step: int = 10) -> tuple[int, ...]:
    if candidate_count <= 0:
        raise ValueError("candidate count must be positive")
    if step <= 0:
        raise ValueError("cutoff step must be positive")
    cutoffs = list(range(step, candidate_count + 1, step))
    if not cutoffs or cutoffs[-1] != candidate_count:
        cutoffs.append(candidate_count)
    return tuple(cutoffs)


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    ndcg: dict[int, float]


def rating_relevance(rating: float) -> float:
    """Map MovieLens positives to four ordered relevance grades."""
    if rating < 3.5:
        return 0.0
    return (rating - 3.0) * 2.0


def ndcg_at_k(
    ranked_movie_ids: Sequence[int],
    relevance_by_movie_id: Mapping[int, float],
    k: int,
) -> float:
    _validate_k(k)
    _validate_unique_ranking(ranked_movie_ids)
    gains = [
        max(0.0, float(relevance_by_movie_id.get(movie_id, 0.0)))
        for movie_id in ranked_movie_ids[:k]
    ]
    dcg = _discounted_gain(gains)
    ideal = sorted(
        (max(0.0, float(value)) for value in relevance_by_movie_id.values()),
        reverse=True,
    )[:k]
    idcg = _discounted_gain(ideal)
    return dcg / idcg if idcg > 0.0 else 0.0


def evaluate_ranking(
    ranked_movie_ids: Sequence[int],
    relevance_by_movie_id: Mapping[int, float],
    *,
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> RankingMetrics:
    return RankingMetrics(
        ndcg={
            int(k): ndcg_at_k(ranked_movie_ids, relevance_by_movie_id, int(k))
            for k in cutoffs
        },
    )


def _discounted_gain(relevances: Sequence[float]) -> float:
    return sum(
        (math.pow(2.0, relevance) - 1.0) / math.log2(rank + 2.0)
        for rank, relevance in enumerate(relevances)
    )


def _validate_k(k: int) -> None:
    if k <= 0:
        raise ValueError("ranking cutoff must be positive")


def _validate_unique_ranking(ranked_movie_ids: Sequence[int]) -> None:
    if len(ranked_movie_ids) != len(set(ranked_movie_ids)):
        raise ValueError("ranked movie IDs cannot contain duplicates")
