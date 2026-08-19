from app.services.recsys.v2.schemas import CandidateScore


def rank_candidates(candidates: list[CandidateScore]) -> list[CandidateScore]:
    by_movie: dict[int, CandidateScore] = {}
    for candidate in candidates:
        existing = by_movie.get(candidate.movie_id)
        if existing is None or candidate.score > existing.score:
            by_movie[candidate.movie_id] = candidate
    return sorted(
        by_movie.values(),
        key=lambda item: (-round(item.score, 12), item.movie_id),
    )
