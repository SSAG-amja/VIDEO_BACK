from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import TypeVar

import numpy as np
from sqlalchemy.orm import Session

from app.crud.recsys.movies import load_movies_by_ids
from app.models.movie import Movie
from app.services.recsys.v3.config import (
    LONG_TERM_COLD_ITEM_GRACE_DAYS,
    LONG_TERM_MATURE_COLD_ITEM_MIN_VOTE_COUNT,
    POLICY_BLOCKED_MOVIE_STATUSES,
)
from app.services.recsys.v3.policy.policy_schemas import MoviePolicyMetadata
from app.services.recsys.v3.retrieval.eligibility_schemas import (
    HardFilterReason,
    HardFilterRejection,
)


_RankedCandidate = TypeVar("_RankedCandidate")


@dataclass(frozen=True, slots=True)
class InitialCandidateSelection:
    candidates: tuple[object, ...]
    rejections: tuple[HardFilterRejection, ...]
    rejection_counts: tuple[tuple[str, int], ...]
    inspected_candidate_count: int


@dataclass(frozen=True, slots=True)
class InitialCatalogMask:
    eligible_item_mask: np.ndarray
    rejection_counts: tuple[tuple[str, int], ...]

    @property
    def eligible_movie_count(self) -> int:
        return int(np.count_nonzero(self.eligible_item_mask))


def identity_supported_movie_ids(
    *,
    movie_ids: Sequence[int],
    item_features,
) -> frozenset[int]:
    movie_count = len(movie_ids)
    diagonal = np.asarray(
        item_features[:movie_count, :movie_count].diagonal(),
        dtype=np.float32,
    ).reshape(-1)
    return frozenset(
        int(movie_id)
        for movie_id, supported in zip(movie_ids, diagonal > 0.0, strict=True)
        if bool(supported)
    )


def select_initial_candidates(
    db: Session,
    *,
    candidates: Sequence[_RankedCandidate],
    as_of: datetime,
    movie_identity_supported: Callable[[int], bool] | None,
    limit: int,
) -> InitialCandidateSelection:
    if limit <= 0:
        raise ValueError("initial candidate selection limit must be positive")
    movie_ids = [int(candidate.movie_id) for candidate in candidates]
    metadata = {
        item.movie_id: item
        for item in map(to_policy_metadata, load_movies_by_ids(db, movie_ids))
    }
    selected: list[_RankedCandidate] = []
    rejections: list[HardFilterRejection] = []
    rejection_counts: Counter[str] = Counter()
    inspected = 0
    for candidate in candidates:
        inspected += 1
        movie_id = int(candidate.movie_id)
        reasons = initial_filter_reasons(
            metadata=metadata.get(movie_id),
            as_of=as_of.date(),
            model_identity_supported=(
                movie_identity_supported(movie_id)
                if movie_identity_supported is not None
                else None
            ),
        )
        if reasons:
            rejections.append(HardFilterRejection(movie_id=movie_id, reasons=reasons))
            rejection_counts.update(reason.value for reason in reasons)
            continue
        selected.append(
            replace(candidate, source_rank=len(selected) + 1)
            if hasattr(candidate, "source_rank")
            else candidate
        )
        if len(selected) >= limit:
            break
    return InitialCandidateSelection(
        candidates=tuple(selected),
        rejections=tuple(rejections),
        rejection_counts=tuple(sorted(rejection_counts.items())),
        inspected_candidate_count=inspected,
    )


def build_initial_catalog_mask(
    db: Session,
    *,
    movie_ids: Sequence[int],
    identity_supported_movie_ids: frozenset[int],
    as_of: datetime,
    chunk_size: int = 8_192,
) -> InitialCatalogMask:
    if chunk_size <= 0:
        raise ValueError("initial catalog filter chunk size must be positive")
    ordered_ids = np.asarray(movie_ids, dtype=np.int64)
    eligible = np.zeros(ordered_ids.size, dtype=np.bool_)
    rejection_counts: Counter[str] = Counter()
    for start in range(0, ordered_ids.size, chunk_size):
        end = min(start + chunk_size, ordered_ids.size)
        chunk = ordered_ids[start:end]
        metadata = {
            item.movie_id: item
            for item in map(
                to_policy_metadata,
                load_movies_by_ids(db, [int(value) for value in chunk]),
            )
        }
        for offset, movie_id_value in enumerate(chunk):
            movie_id = int(movie_id_value)
            reasons = initial_filter_reasons(
                metadata=metadata.get(movie_id),
                as_of=as_of.date(),
                model_identity_supported=movie_id in identity_supported_movie_ids,
            )
            if reasons:
                rejection_counts.update(reason.value for reason in reasons)
            else:
                eligible[start + offset] = True
    return InitialCatalogMask(
        eligible_item_mask=eligible,
        rejection_counts=tuple(sorted(rejection_counts.items())),
    )


def initial_filter_reasons(
    *,
    metadata: MoviePolicyMetadata | None,
    as_of: date,
    model_identity_supported: bool | None,
) -> tuple[HardFilterReason, ...]:
    if metadata is None:
        return (HardFilterReason.MISSING_MOVIE,)
    reasons: list[HardFilterReason] = []
    if metadata.adult:
        reasons.append(HardFilterReason.ADULT)
    if not ((metadata.title_ko or "").strip() or (metadata.title or "").strip()):
        reasons.append(HardFilterReason.MISSING_TITLE)
    if metadata.status in POLICY_BLOCKED_MOVIE_STATUSES:
        reasons.append(HardFilterReason.BLOCKED_STATUS)
    if model_identity_supported is False and is_untrusted_mature_cold_item(
        metadata,
        as_of=as_of,
    ):
        reasons.append(HardFilterReason.UNTRUSTED_MATURE_COLD_ITEM)
    return tuple(reasons)


def is_untrusted_mature_cold_item(
    metadata: MoviePolicyMetadata,
    *,
    as_of: date,
) -> bool:
    if metadata.release_date is not None:
        age_days = (as_of - metadata.release_date).days
        if 0 <= age_days <= LONG_TERM_COLD_ITEM_GRACE_DAYS:
            return False
    return metadata.vote_count < LONG_TERM_MATURE_COLD_ITEM_MIN_VOTE_COUNT


def to_policy_metadata(movie: Movie) -> MoviePolicyMetadata:
    return MoviePolicyMetadata(
        movie_id=int(movie.id),
        adult=bool(movie.adult),
        title=movie.title,
        title_ko=movie.title_ko,
        status=movie.status,
        popularity=max(float(movie.popularity or 0.0), 0.0),
        vote_average=max(float(movie.vote_average or 0.0), 0.0),
        vote_count=max(int(movie.vote_count or 0), 0),
        release_date=movie.release_date,
    )
