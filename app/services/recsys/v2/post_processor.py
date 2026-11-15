from app.services.recsys.v2.schemas import CandidateScore, UserProfile


def apply_safety_filters(
    candidates: list[CandidateScore],
    *,
    profile: UserProfile,
    extra_excluded_movie_ids: set[int] | None = None,
) -> list[CandidateScore]:
    excluded = profile.excluded_movie_ids | profile.negative_movie_ids | (extra_excluded_movie_ids or set())
    return [candidate for candidate in candidates if candidate.movie_id not in excluded]
