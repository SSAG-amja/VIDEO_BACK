from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.services.recsys.v3.config import (
    SHORT_TERM_CANDIDATE_CACHE_FORMAT_VERSION,
    SHORT_TERM_CANDIDATE_CACHE_TTL_JITTER_SECONDS,
    SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS,
    SHORT_TERM_RETRIEVAL_LIMIT,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    ShortTermCandidate,
    ShortTermRetrievalDiagnostics,
    ShortTermRetrievalResult,
)
from app.services.recsys.v3.domain.schemas import UserProfileBundle
from app.services.recsys.v3.retrieval.short_term_retriever import retrieve_short_term_candidates


logger = logging.getLogger(__name__)


def short_term_candidate_cache_key(user_id: int) -> str:
    if user_id <= 0:
        raise ValueError("short-term cache user ID must be positive")
    return f"user:{user_id}:v3:short_term_candidates"


def short_term_profile_signature(
    profile: UserProfileBundle,
    *,
    ontology_build_id: int,
) -> str:
    if ontology_build_id <= 0:
        raise ValueError("short-term cache ontology build ID must be positive")
    payload = {
        "format_version": SHORT_TERM_CANDIDATE_CACHE_FORMAT_VERSION,
        "ontology_build_id": ontology_build_id,
        "user_id": profile.user_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def retrieve_cached_short_term_candidates(
    db: Session,
    *,
    redis: Redis | None,
    ontology_build_id: int,
    profile: UserProfileBundle,
    limit: int = SHORT_TERM_RETRIEVAL_LIMIT,
    force_refresh: bool = False,
) -> ShortTermRetrievalResult:
    if limit <= 0 or limit > SHORT_TERM_RETRIEVAL_LIMIT:
        raise ValueError(
            f"short-term retrieval limit must be between 1 and {SHORT_TERM_RETRIEVAL_LIMIT}"
        )
    started = time.monotonic()
    signature = short_term_profile_signature(
        profile,
        ontology_build_id=ontology_build_id,
    )
    excluded_movie_ids = frozenset(
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
    )

    cache_status = "disabled"
    if redis is not None and not force_refresh:
        cached, cache_status = _load_cache(
            redis,
            user_id=profile.user_id,
            expected_signature=signature,
            ontology_build_id=ontology_build_id,
            limit=limit,
            excluded_movie_ids=excluded_movie_ids,
        )
        if cached is not None:
            return ShortTermRetrievalResult(
                candidates=cached,
                diagnostics=ShortTermRetrievalDiagnostics(
                    ontology_build_id=ontology_build_id,
                    profile_feature_count=len(profile.short_term.positive_features),
                    excluded_movie_count=len(
                        profile.long_term.excluded_movie_ids
                        | profile.short_term.recent_negative_movie_ids
                    ),
                    candidate_count=len(cached),
                    elapsed_seconds=round(time.monotonic() - started, 6),
                    query_count=0,
                    cache_status=cache_status,
                    profile_signature=signature,
                ),
            )

    computed = retrieve_short_term_candidates(
        db,
        ontology_build_id=ontology_build_id,
        profile=profile,
        limit=limit,
    )
    stored = False
    if redis is not None:
        stored = _store_cache(
            redis,
            user_id=profile.user_id,
            ontology_build_id=ontology_build_id,
            signature=signature,
            candidates=computed.candidates,
            retrieval_limit=limit,
        )
    if force_refresh:
        final_status = "refresh_stored" if stored else "refresh_store_failed"
    elif stored:
        final_status = "miss_stored"
    elif redis is None:
        final_status = "disabled"
    else:
        final_status = f"{cache_status}_store_failed"
    return replace(
        computed,
        diagnostics=replace(
            computed.diagnostics,
            elapsed_seconds=round(time.monotonic() - started, 6),
            cache_status=final_status,
            profile_signature=signature,
        ),
    )


def _load_cache(
    redis: Redis,
    *,
    user_id: int,
    expected_signature: str,
    ontology_build_id: int,
    limit: int,
    excluded_movie_ids: frozenset[int],
) -> tuple[tuple[ShortTermCandidate, ...] | None, str]:
    try:
        raw = redis.get(short_term_candidate_cache_key(user_id))
    except RedisError:
        logger.warning("short-term candidate cache read failed user_id=%s", user_id, exc_info=True)
        return None, "read_failed"
    if raw is None:
        return None, "miss"
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None, "invalid"
        if payload.get("format_version") != SHORT_TERM_CANDIDATE_CACHE_FORMAT_VERSION:
            return None, "version_mismatch"
        if payload.get("ontology_build_id") != ontology_build_id:
            return None, "build_mismatch"
        if payload.get("profile_signature") != expected_signature:
            return None, "profile_changed"
        if int(payload.get("retrieval_limit", 0)) < limit:
            return None, "limit_mismatch"
        rows = payload.get("candidates")
        if not isinstance(rows, list) or len(rows) > SHORT_TERM_RETRIEVAL_LIMIT:
            return None, "invalid"
        loaded = tuple(
            ShortTermCandidate(
                movie_id=int(row["movie_id"]),
                short_term_raw_score=float(row["score"]),
                source_rank=int(row["rank"]),
            )
            for row in rows[:limit]
        )
        if len({item.movie_id for item in loaded}) != len(loaded):
            return None, "invalid"
        if [item.source_rank for item in loaded] != list(range(1, len(loaded) + 1)):
            return None, "invalid"
        candidates = tuple(
            replace(item, source_rank=rank)
            for rank, item in enumerate(
                (item for item in loaded if item.movie_id not in excluded_movie_ids),
                start=1,
            )
        )
        return candidates, ("hit_filtered" if len(candidates) != len(loaded) else "hit")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "invalid short-term candidate cache user_id=%s error=%s",
            user_id,
            type(exc).__name__,
        )
        return None, "invalid"


def _store_cache(
    redis: Redis,
    *,
    user_id: int,
    ontology_build_id: int,
    signature: str,
    candidates: tuple[ShortTermCandidate, ...],
    retrieval_limit: int,
) -> bool:
    if len(candidates) > retrieval_limit or len(candidates) > SHORT_TERM_RETRIEVAL_LIMIT:
        raise ValueError("short-term cache candidate count exceeds retrieval limit")
    if len({item.movie_id for item in candidates}) != len(candidates):
        raise ValueError("short-term cache candidates contain duplicate movies")
    if [item.source_rank for item in candidates] != list(range(1, len(candidates) + 1)):
        raise ValueError("short-term cache candidate ranks must be contiguous")
    payload = {
        "format_version": SHORT_TERM_CANDIDATE_CACHE_FORMAT_VERSION,
        "ontology_build_id": ontology_build_id,
        "profile_signature": signature,
        "retrieval_limit": retrieval_limit,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [
            {
                "movie_id": item.movie_id,
                "score": item.short_term_raw_score,
                "rank": item.source_rank,
            }
            for item in candidates
        ],
    }
    try:
        redis.set(
            short_term_candidate_cache_key(user_id),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ex=(
                SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS
                + user_id % (SHORT_TERM_CANDIDATE_CACHE_TTL_JITTER_SECONDS + 1)
            ),
        )
        return True
    except RedisError:
        logger.warning("short-term candidate cache write failed user_id=%s", user_id, exc_info=True)
        return False
