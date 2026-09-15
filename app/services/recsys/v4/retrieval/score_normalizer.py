from __future__ import annotations

import math
from collections.abc import Mapping


def percentile_normalize(scores_by_movie: Mapping[int, float]) -> dict[int, float]:
    if not scores_by_movie:
        return {}
    normalized_input = {int(movie_id): float(score) for movie_id, score in scores_by_movie.items()}
    if any(not math.isfinite(score) for score in normalized_input.values()):
        raise ValueError("source normalization requires finite scores")
    if len(normalized_input) == 1 or len(set(normalized_input.values())) == 1:
        return {movie_id: 0.5 for movie_id in normalized_input}

    ordered = sorted(normalized_input.items(), key=lambda item: (item[1], item[0]))
    denominator = len(ordered) - 1
    result: dict[int, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        score = ordered[start][1]
        while end < len(ordered) and ordered[end][1] == score:
            end += 1
        percentile = ((start + end - 1) / 2.0) / denominator
        for index in range(start, end):
            result[ordered[index][0]] = round(percentile, 8)
        start = end
    return result
