from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.services.recsys.v3.config import (
    CANDIDATE_CHECKPOINT_USER_COUNT,
    CANDIDATE_ITEM_BLOCK_SIZE,
    CANDIDATE_MATERIALIZATION_WORKER_COUNT,
    CANDIDATE_STORAGE_SIZE,
    CANDIDATE_USER_BLOCK_SIZE,
)


@dataclass(frozen=True, slots=True)
class CandidateMaterializationConfig:
    top_k: int = CANDIDATE_STORAGE_SIZE
    user_block_size: int = CANDIDATE_USER_BLOCK_SIZE
    item_block_size: int = CANDIDATE_ITEM_BLOCK_SIZE
    checkpoint_user_count: int = CANDIDATE_CHECKPOINT_USER_COUNT
    worker_count: int = CANDIDATE_MATERIALIZATION_WORKER_COUNT

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.top_k > CANDIDATE_STORAGE_SIZE:
            raise ValueError(f"top_k cannot exceed {CANDIDATE_STORAGE_SIZE}")
        if self.checkpoint_user_count < self.user_block_size:
            raise ValueError("checkpoint_user_count cannot be smaller than user_block_size")

    @property
    def result_config(self) -> dict[str, int]:
        payload = asdict(self)
        payload.pop("worker_count")
        return payload

    @property
    def execution_config(self) -> dict[str, int]:
        return {"worker_count": self.worker_count}

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.result_config, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateFailure:
    user_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    successful_user_ids: np.ndarray
    candidate_user_ids: np.ndarray
    movie_ids: np.ndarray
    model_scores: np.ndarray
    source_ranks: np.ndarray
    failures: tuple[CandidateFailure, ...]
    elapsed_seconds: float
    peak_score_block_bytes: int

    @property
    def candidate_count(self) -> int:
        return int(self.movie_ids.size)


@dataclass(frozen=True, slots=True)
class LoadedCandidateSnapshot:
    path: Path
    manifest: dict[str, Any]

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["candidate_snapshot_id"])

    @property
    def model_build_id(self) -> str:
        return str(self.manifest["model_build_id"])
