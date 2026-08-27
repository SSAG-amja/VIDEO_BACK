from __future__ import annotations

import math

from app.services.recsys.v3.config import (
    POLICY_QUALITY_POPULARITY_REFERENCE,
    POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR,
)


def reliable_quality_score(*, popularity: float, vote_average: float, vote_count: int) -> float:
    safe_popularity = max(float(popularity), 0.0)
    safe_rating = max(float(vote_average), 0.0)
    safe_count = max(int(vote_count), 0)
    confidence = safe_count / (safe_count + POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR)
    rating = min(safe_rating / 10.0, 1.0)
    popularity_score = min(
        math.log1p(safe_popularity) / math.log1p(POLICY_QUALITY_POPULARITY_REFERENCE),
        1.0,
    )
    return confidence * ((0.85 * rating) + (0.15 * popularity_score))
