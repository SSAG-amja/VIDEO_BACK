from __future__ import annotations

import hashlib
import json

from app.services.recsys.v3 import config


_POLICY_CONFIG_KEYS = (
    "POLICY_PERSONAL_COMPONENT_WEIGHT",
    "POLICY_ONTOLOGY_COMPONENT_WEIGHT",
    "POLICY_ONTOLOGY_SHORT_TERM_MULTIPLIER",
    "POLICY_BLOCKED_MOVIE_STATUSES",
    "POLICY_OTT_BONUS_MAX",
    "POLICY_RECENCY_BONUS_MAX",
    "POLICY_RECENCY_WINDOW_DAYS",
    "POLICY_QUALITY_BONUS_MAX",
    "POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR",
    "POLICY_QUALITY_POPULARITY_REFERENCE",
    "POLICY_NEGATIVE_MAX_BASE_RATIO",
    "POLICY_NEGATIVE_MAX_ABSOLUTE",
    "POLICY_NEGATIVE_CONFIDENCE_PAIR_COUNT",
    "POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER",
    "POLICY_NEGATIVE_FEATURE_WEIGHTS",
    "POLICY_MMR_SIMILARITY_PENALTY_MAX",
    "POLICY_REPETITION_PENALTY_MAX",
    "COLD_START_FEATURE_ONLY_MODEL_WEIGHT",
    "COLD_START_GENRE_ONLY_MODEL_WEIGHT",
    "COLD_START_OVERVIEW_SUPPORT_BONUS_MAX",
    "COLD_START_GENRE_ONLY_SEMANTIC_WEIGHT",
    "COLD_START_GENRE_ONLY_QUALITY_WEIGHT",
    "COLD_START_GENRE_COVERAGE_WEIGHT",
    "COLD_START_GENRE_SPECIFICITY_WEIGHT",
    "COLD_START_GENRE_ONLY_MIN_VOTE_COUNT",
    "COLD_START_GENRE_ONLY_TRUSTED_VOTE_COUNT",
)


def policy_config_snapshot() -> dict:
    values = {
        key: _json_value(getattr(config, key))
        for key in _POLICY_CONFIG_KEYS
    }
    return {
        "policy_config_version": config.POLICY_CONFIG_VERSION,
        "values": values,
    }


def policy_config_hash(snapshot: dict | None = None) -> str:
    payload = snapshot or policy_config_snapshot()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _json_value(value):
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    return value
