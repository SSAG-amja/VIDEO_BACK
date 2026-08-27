from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HardFilterReason(StrEnum):
    MISSING_MOVIE = "missing_movie"
    ADULT = "adult"
    MISSING_TITLE = "missing_title"
    WATCHED = "watched"
    PASSED = "passed"
    BLACKLISTED = "blacklisted"
    SESSION_EXPOSED = "session_exposed"
    BLOCKED_MOVIE = "blocked_movie"
    BLOCKED_STATUS = "blocked_status"
    NOT_ON_SUBSCRIBED_OTT = "not_on_subscribed_ott"
    COLD_START_NO_VOTES = "cold_start_no_votes"


@dataclass(frozen=True, slots=True)
class HardFilterRejection:
    movie_id: int
    reasons: tuple[HardFilterReason, ...]

    def __post_init__(self) -> None:
        if self.movie_id <= 0 or not self.reasons:
            raise ValueError("hard filter rejection requires a movie and at least one reason")


@dataclass(frozen=True, slots=True)
class CandidateEligibilityDiagnostics:
    input_candidate_count: int = 0
    inspected_candidate_count: int = 0
    selected_candidate_count: int = 0
    rejected_candidate_count: int = 0
    reserve_selected_count: int = 0
    rejection_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.input_candidate_count,
            self.inspected_candidate_count,
            self.selected_candidate_count,
            self.rejected_candidate_count,
            self.reserve_selected_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("candidate eligibility counts cannot be negative")
        if self.inspected_candidate_count > self.input_candidate_count:
            raise ValueError("inspected candidate count cannot exceed input count")
        if self.selected_candidate_count + self.rejected_candidate_count != self.inspected_candidate_count:
            raise ValueError("candidate eligibility inspected count is inconsistent")
        if self.reserve_selected_count > self.selected_candidate_count:
            raise ValueError("reserve selection cannot exceed selected candidates")
        if any(not reason or count <= 0 for reason, count in self.rejection_counts):
            raise ValueError("candidate eligibility rejection counts are invalid")
