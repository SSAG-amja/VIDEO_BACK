from app.services.recsys.v2.config import DEFAULT_SCORE_CONFIG
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile, UserProfile


def score_candidates(
    candidates: list[CandidateScore],
    *,
    profile: UserProfile,
    session_profile: SessionProfile,
    score_config: dict | None = None,
) -> list[CandidateScore]:
    config = score_config or DEFAULT_SCORE_CONFIG
    adjusted: list[CandidateScore] = []
    quality_bonus_ratio = float(config.get("thresholds", {}).get("max_quality_bonus_ratio", 0.2))
    session_score_ratio = float(config.get("thresholds", {}).get("max_session_score_ratio", 0.35))

    for candidate in candidates:
        base_score = candidate.score
        source_scores = dict(candidate.source_scores)
        session_adjustment = session_concept_adjustment(candidate, session_profile)
        max_session_adjustment = abs(base_score) * session_score_ratio
        if session_adjustment > max_session_adjustment:
            session_adjustment = max_session_adjustment
        elif session_adjustment < -max_session_adjustment:
            session_adjustment = -max_session_adjustment

        quality_bonus = source_scores.get("popularity", 0.0) + source_scores.get("rating", 0.0)
        max_quality_bonus = abs(base_score) * quality_bonus_ratio
        if quality_bonus > max_quality_bonus:
            quality_bonus = max_quality_bonus

        source_scores["session_adjustment"] = session_adjustment
        source_scores["quality_capped"] = quality_bonus
        adjusted.append(
            CandidateScore(
                movie_id=candidate.movie_id,
                score=base_score + session_adjustment + quality_bonus,
                source=candidate.source,
                source_scores=source_scores,
                explanation_tags=candidate.explanation_tags,
                metadata={**candidate.metadata, "profile_type": profile.profile_type},
            )
        )
    return adjusted


def session_concept_adjustment(candidate: CandidateScore, session_profile: SessionProfile) -> float:
    adjustment = 0.0
    tags = set(candidate.explanation_tags)
    for concept, score in session_profile.session_positive_concept_scores.items():
        if concept in tags:
            adjustment += float(score)
    for concept, score in session_profile.session_negative_concept_scores.items():
        if concept in tags:
            adjustment -= float(score)
    return adjustment
