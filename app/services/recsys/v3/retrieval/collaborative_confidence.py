from __future__ import annotations

import math
from dataclasses import dataclass

from app.services.recsys.v3.config import (
    COLLABORATIVE_CANDIDATE_JACCARD_BAD,
    COLLABORATIVE_CANDIDATE_JACCARD_GOOD,
    COLLABORATIVE_INPUT_MAX_SUPPORT_BAD,
    COLLABORATIVE_INPUT_MAX_SUPPORT_GOOD,
    COLLABORATIVE_POPULATION_FULL_USERS,
    COLLABORATIVE_POPULATION_MIN_USERS,
    COLLABORATIVE_USER_FULL_POSITIVE_PAIRS,
    COLLABORATIVE_USER_MIN_POSITIVE_PAIRS,
)


@dataclass(frozen=True, slots=True)
class CollaborativeConfidence:
    population_confidence: float
    user_evidence_confidence: float
    effective_confidence: float


def assess_population_collaborative_confidence(
    *,
    user_count: int,
    max_item_user_support_ratio: float | None,
    candidate_pairwise_jaccard: float | None,
) -> dict[str, float | int | None]:
    if user_count < 0:
        raise ValueError("collaborative population user count cannot be negative")
    for name, value in (
        ("max item user support ratio", max_item_user_support_ratio),
        ("candidate pairwise jaccard", candidate_pairwise_jaccard),
    ):
        if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
            raise ValueError(f"{name} must be in [0, 1]")

    population_size = _ascending_confidence(
        user_count,
        low=COLLABORATIVE_POPULATION_MIN_USERS,
        high=COLLABORATIVE_POPULATION_FULL_USERS,
    )
    input_diversity = (
        _descending_confidence(
            max_item_user_support_ratio,
            good=COLLABORATIVE_INPUT_MAX_SUPPORT_GOOD,
            bad=COLLABORATIVE_INPUT_MAX_SUPPORT_BAD,
        )
        if max_item_user_support_ratio is not None
        else 0.5
    )
    candidate_diversity = (
        _descending_confidence(
            candidate_pairwise_jaccard,
            good=COLLABORATIVE_CANDIDATE_JACCARD_GOOD,
            bad=COLLABORATIVE_CANDIDATE_JACCARD_BAD,
        )
        if candidate_pairwise_jaccard is not None
        else 0.5
    )
    confidence = min(population_size, input_diversity, candidate_diversity)
    return {
        "user_count": user_count,
        "population_size_confidence": round(population_size, 8),
        "input_diversity_confidence": round(input_diversity, 8),
        "candidate_diversity_confidence": round(candidate_diversity, 8),
        "max_item_user_support_ratio": max_item_user_support_ratio,
        "candidate_pairwise_jaccard": candidate_pairwise_jaccard,
        "confidence": round(confidence, 8),
    }


def assess_user_collaborative_confidence(
    *,
    positive_pair_count: int,
    population_confidence: float,
) -> CollaborativeConfidence:
    if positive_pair_count < 0:
        raise ValueError("positive pair count cannot be negative")
    if not math.isfinite(population_confidence) or not 0.0 <= population_confidence <= 1.0:
        raise ValueError("population confidence must be in [0, 1]")
    user_evidence = _ascending_confidence(
        positive_pair_count,
        low=COLLABORATIVE_USER_MIN_POSITIVE_PAIRS,
        high=COLLABORATIVE_USER_FULL_POSITIVE_PAIRS,
    )
    return CollaborativeConfidence(
        population_confidence=round(population_confidence, 8),
        user_evidence_confidence=round(user_evidence, 8),
        effective_confidence=round(min(population_confidence, user_evidence), 8),
    )


def population_confidence_from_diagnostics(
    diagnostics: dict,
    *,
    user_count: int,
) -> float:
    reliability = diagnostics.get("collaborative_reliability")
    if isinstance(reliability, dict) and "confidence" in reliability:
        confidence = float(reliability["confidence"])
        if math.isfinite(confidence) and 0.0 <= confidence <= 1.0:
            return confidence
    candidate_sample = diagnostics.get("model_health", {}).get(
        "candidate_sample",
        {},
    )
    assessment = assess_population_collaborative_confidence(
        user_count=user_count,
        max_item_user_support_ratio=None,
        candidate_pairwise_jaccard=_optional_unit_float(
            candidate_sample.get("pairwise_jaccard_mean")
        ),
    )
    return float(assessment["confidence"])


def _ascending_confidence(value: float, *, low: float, high: float) -> float:
    if high <= low:
        raise ValueError("confidence upper bound must exceed lower bound")
    return min(1.0, max(0.0, (float(value) - low) / (high - low)))


def _descending_confidence(value: float, *, good: float, bad: float) -> float:
    if bad <= good:
        raise ValueError("confidence bad bound must exceed good bound")
    return min(1.0, max(0.0, (bad - float(value)) / (bad - good)))


def _optional_unit_float(value: object) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None
