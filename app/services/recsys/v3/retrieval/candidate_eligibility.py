from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.crud.recsys.movies import load_movies_by_ids, load_streaming_movie_ids
from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    COLD_START_GENRE_ONLY_MIN_VOTE_COUNT,
)
from app.services.recsys.v3.retrieval.initial_candidate_filter import (
    initial_filter_reasons,
    to_policy_metadata,
)
from app.services.recsys.v3.retrieval.eligibility_schemas import (
    CandidateEligibilityDiagnostics,
    HardFilterReason,
    HardFilterRejection,
)
from app.services.recsys.v3.policy.policy_schemas import MoviePolicyMetadata, PolicyRequestContext
from app.services.recsys.v3.retrieval.retrieval_schemas import MergedCandidate
from app.services.recsys.v3.domain.schemas import OttFilterMode, UserProfileBundle


@dataclass(frozen=True, slots=True)
class CandidateEligibilitySelection:
    candidates: tuple[MergedCandidate, ...]
    rejections: tuple[HardFilterRejection, ...]
    diagnostics: CandidateEligibilityDiagnostics


def select_eligible_candidates(
    db: Session,
    *,
    candidates: Sequence[MergedCandidate],
    profile: UserProfileBundle,
    context: PolicyRequestContext,
    movie_identity_supported: Callable[[int], bool] | None = None,
    limit: int = CANDIDATE_POOL_SIZE,
) -> CandidateEligibilitySelection:
    if limit <= 0 or limit > CANDIDATE_POOL_SIZE:
        raise ValueError(f"candidate eligibility limit must be between 1 and {CANDIDATE_POOL_SIZE}")
    movie_ids = [candidate.movie_id for candidate in candidates]
    metadata = {
        item.movie_id: item
        for item in map(to_policy_metadata, load_movies_by_ids(db, movie_ids))
    }
    subscribed_movie_ids = (
        load_streaming_movie_ids(
            db,
            list(profile.serving_context.subscribed_ott_ids),
            movie_ids,
        )
        if profile.serving_context.ott_mode == OttFilterMode.SUBSCRIBED_ONLY
        else set()
    )

    selected: list[MergedCandidate] = []
    rejections: list[HardFilterRejection] = []
    rejection_counts: Counter[str] = Counter()
    reserve_selected_count = 0
    for index, candidate in enumerate(candidates):
        reasons = hard_filter_reasons(
            candidate.movie_id,
            metadata=metadata.get(candidate.movie_id),
            on_subscribed_ott=candidate.movie_id in subscribed_movie_ids,
            profile=profile,
            context=context,
            model_identity_supported=(
                movie_identity_supported(candidate.movie_id)
                if movie_identity_supported is not None
                else None
            ),
        )
        if reasons:
            rejections.append(HardFilterRejection(movie_id=candidate.movie_id, reasons=reasons))
            rejection_counts.update(reason.value for reason in reasons)
            continue
        selected.append(candidate)
        if index >= CANDIDATE_POOL_SIZE:
            reserve_selected_count += 1
        if len(selected) >= limit:
            break

    inspected_count = len(selected) + len(rejections)
    return CandidateEligibilitySelection(
        candidates=tuple(selected),
        rejections=tuple(rejections),
        diagnostics=CandidateEligibilityDiagnostics(
            input_candidate_count=len(candidates),
            inspected_candidate_count=inspected_count,
            selected_candidate_count=len(selected),
            rejected_candidate_count=len(rejections),
            reserve_selected_count=reserve_selected_count,
            rejection_counts=tuple(sorted(rejection_counts.items())),
        ),
    )


def hard_filter_reasons(
    movie_id: int,
    *,
    metadata: MoviePolicyMetadata | None,
    on_subscribed_ott: bool,
    profile: UserProfileBundle,
    context: PolicyRequestContext,
    model_identity_supported: bool | None = None,
) -> tuple[HardFilterReason, ...]:
    """Compatibility entry point combining initial defense and request filtering."""
    initial_reasons = initial_filter_reasons(
        metadata=metadata,
        as_of=context.as_of.date(),
        model_identity_supported=model_identity_supported,
    )
    request_reasons = request_filter_reasons(
        movie_id,
        metadata=metadata,
        on_subscribed_ott=on_subscribed_ott,
        profile=profile,
        context=context,
    )
    return tuple(dict.fromkeys((*initial_reasons, *request_reasons)))


def request_filter_reasons(
    movie_id: int,
    *,
    metadata: MoviePolicyMetadata | None,
    on_subscribed_ott: bool,
    profile: UserProfileBundle,
    context: PolicyRequestContext,
) -> tuple[HardFilterReason, ...]:
    reasons: list[HardFilterReason] = []
    if metadata is None:
        return (HardFilterReason.MISSING_MOVIE,)
    if movie_id in profile.long_term.negative_movie_ids:
        reasons.append(HardFilterReason.PASSED)
    elif movie_id in profile.long_term.excluded_movie_ids:
        reasons.append(HardFilterReason.WATCHED)
    if movie_id in context.blacklisted_movie_ids:
        reasons.append(HardFilterReason.BLACKLISTED)
    if movie_id in context.session_exposed_movie_ids:
        reasons.append(HardFilterReason.SESSION_EXPOSED)
    if movie_id in context.blocked_movie_ids:
        reasons.append(HardFilterReason.BLOCKED_MOVIE)
    if metadata.status in context.blocked_statuses:
        reasons.append(HardFilterReason.BLOCKED_STATUS)
    if context.genre_only_cold_start and metadata.vote_count < COLD_START_GENRE_ONLY_MIN_VOTE_COUNT:
        reasons.append(HardFilterReason.COLD_START_NO_VOTES)
    if (
        profile.serving_context.ott_mode == OttFilterMode.SUBSCRIBED_ONLY
        and not on_subscribed_ott
    ):
        reasons.append(HardFilterReason.NOT_ON_SUBSCRIBED_OTT)
    return tuple(dict.fromkeys(reasons))
