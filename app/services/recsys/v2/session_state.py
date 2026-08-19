import json
import uuid

from redis import Redis

from app.services.recsys.v2.config import SESSION_TTL_SECONDS
from app.services.recsys.v2.schemas import SessionProfile


def new_request_id() -> str:
    return uuid.uuid4().hex


def new_feed_session_key() -> str:
    return uuid.uuid4().hex


def _session_key(feed_session_key: str) -> str:
    return f"recsys:v2:session:{feed_session_key}"


def load_session_profile(redis: Redis, feed_session_key: str) -> SessionProfile:
    raw = redis.get(_session_key(feed_session_key))
    if not raw:
        return SessionProfile(feed_session_key=feed_session_key)
    payload = json.loads(raw)
    return SessionProfile(
        feed_session_key=feed_session_key,
        refresh_count=int(payload.get("refresh_count", 0)),
        recently_exposed_movie_ids=set(payload.get("recently_exposed_movie_ids", [])),
        recent_skipped_movie_ids=set(payload.get("recent_skipped_movie_ids", [])),
        recent_dwell_map={int(k): int(v) for k, v in payload.get("recent_dwell_map", {}).items()},
        session_positive_concept_scores=payload.get("session_positive_concept_scores", {}),
        session_negative_concept_scores=payload.get("session_negative_concept_scores", {}),
    )


def save_session_profile(redis: Redis, profile: SessionProfile) -> None:
    payload = {
        "refresh_count": profile.refresh_count,
        "recently_exposed_movie_ids": sorted(profile.recently_exposed_movie_ids),
        "recent_skipped_movie_ids": sorted(profile.recent_skipped_movie_ids),
        "recent_dwell_map": {str(k): v for k, v in profile.recent_dwell_map.items()},
        "session_positive_concept_scores": profile.session_positive_concept_scores,
        "session_negative_concept_scores": profile.session_negative_concept_scores,
    }
    redis.setex(_session_key(profile.feed_session_key), SESSION_TTL_SECONDS, json.dumps(payload))
