from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.services.recsys.v3.domain.behavior import SnapshotAction, SnapshotSignal


class SocialSignalAction(StrEnum):
    MOVIE_POST_WRITE = "movie_post_write"
    PLAYLIST_POST_WRITE = "playlist_post_write"
    MOVIE_POST_LIKE = "movie_post_like"
    PLAYLIST_POST_LIKE = "playlist_post_like"
    MOVIE_POST_REPLY = "movie_post_reply"
    PLAYLIST_POST_REPLY = "playlist_post_reply"


@dataclass(frozen=True, slots=True)
class SocialRawSignal:
    user_id: int
    movie_id: int
    action: SocialSignalAction
    source_id: int
    post_id: int
    playlist_id: int | None
    occurred_at: datetime | None
    distributed_unit: float
    eligible_for_training: bool
    eligibility_reason: str


@dataclass(frozen=True, slots=True)
class SocialProjectionDiagnostics:
    raw_signal_count: int
    eligible_signal_count: int
    action_signal_counts: dict[str, int]
    action_event_counts: dict[str, int]
    action_unit_totals: dict[str, float]
    deferred_event_counts: dict[str, int]
    missing_timestamp_count: int
    projection_hash: str


@dataclass(frozen=True, slots=True)
class SocialProjectionResult:
    signals: tuple[SocialRawSignal, ...]
    diagnostics: SocialProjectionDiagnostics


@dataclass(frozen=True, slots=True)
class PositiveInteraction:
    user_id: int
    movie_id: int
    actions: tuple[SnapshotAction, ...]
    representative_action: SnapshotAction
    sample_weight: float
    latest_at: datetime | None


@dataclass(frozen=True, slots=True)
class DatasetDiagnostics:
    data_cutoff_at: datetime
    catalog_movie_count: int
    model_user_count: int
    positive_pair_count: int
    raw_signal_count: int
    action_signal_counts: dict[str, int]
    passed_movie_count: int
    watched_movie_count: int
    excluded_pair_count: int
    passed_positive_conflict_count: int
    missing_timestamp_count: int
    dataset_hash: str
    social_raw_signal_count: int = 0
    social_eligible_signal_count: int = 0
    social_action_signal_counts: dict[str, int] = field(default_factory=dict)
    social_deferred_event_counts: dict[str, int] = field(default_factory=dict)
    social_projection_hash: str = ""


@dataclass(frozen=True, slots=True)
class LightFMDatasetSnapshot:
    data_cutoff_at: datetime
    user_ids: tuple[int, ...]
    movie_ids: tuple[int, ...]
    user_id_map: dict[int, int]
    movie_id_map: dict[int, int]
    interactions: Any
    sample_weights: Any
    positives: tuple[PositiveInteraction, ...]
    social_signals: tuple[SocialRawSignal, ...] = ()
    social_projection_diagnostics: SocialProjectionDiagnostics | None = None
    excluded_movie_ids_by_user: dict[int, frozenset[int]] = field(default_factory=dict)
    passed_movie_ids_by_user: dict[int, frozenset[int]] = field(default_factory=dict)
    watched_movie_ids_by_user: dict[int, frozenset[int]] = field(default_factory=dict)
    diagnostics: DatasetDiagnostics | None = None
