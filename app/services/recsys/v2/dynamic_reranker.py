from app.services.recsys.v2.schemas import CandidateScore, SessionProfile


def rerank_for_session(candidates: list[CandidateScore], *, session_profile: SessionProfile) -> list[CandidateScore]:
    exposed = session_profile.recently_exposed_movie_ids
    skipped = session_profile.recent_skipped_movie_ids
    adjusted: list[CandidateScore] = []
    for candidate in candidates:
        penalty = 0.0
        if candidate.movie_id in exposed:
            penalty += 0.2
            candidate.source_scores["exposure_penalty"] = -0.2
        if candidate.movie_id in skipped:
            penalty += 0.5
            candidate.source_scores["skip_penalty"] = -0.5
        adjusted.append(
            CandidateScore(
                movie_id=candidate.movie_id,
                score=candidate.score - penalty,
                source=candidate.source,
                source_scores=candidate.source_scores,
                explanation_tags=candidate.explanation_tags,
                metadata=candidate.metadata,
            )
        )
    return sorted(
        adjusted,
        key=lambda item: (-round(item.score, 12), item.movie_id),
    )
