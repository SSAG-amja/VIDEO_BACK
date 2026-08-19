ENGINE_NAME = "ontology_v2"
ENGINE_VERSION = "v2.0.0"

PROFILE_ACTION_FEATURE_WEIGHTS = {
    "favorite": {
        "genre": 2.5,
        "keyword": 1.8,
        "actor": 1.0,
        "director": 1.5,
    },
    "pinned": {
        "genre": 3.0,
        "keyword": 2.2,
        "actor": 1.2,
        "director": 1.8,
    },
    "saved": {
        "genre": 3.0,
        "keyword": 2.2,
        "actor": 1.2,
        "director": 1.8,
    },
    "watched": {
        "genre": 1.0,
        "keyword": 0.8,
        "actor": 0.4,
        "director": 0.6,
    },
}
PREFERRED_GENRE_WEIGHT = 4.0
PROFILE_FEATURE_LIMITS = {
    "genre": 20,
    "keyword": 80,
    "actor": 80,
    "director": 40,
    "theme": 40,
    "mood": 24,
}
DIRECT_RELATION_WEIGHTS = {
    "has_genre": 1.0,
    "has_keyword": 0.7,
    "has_actor": 0.5,
    "has_director": 0.9,
    "available_on": 0.0,
}

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
DEFAULT_CANDIDATE_SLICE_SIZE = 80
ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD = False
ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD = False
ENABLE_ACTOR_NODES_IN_GRAPH_BUILD = False
GRAPH_BUILD_BATCH_SIZE = 100_000

MIN_CANDIDATE_VOTE_COUNT = 20
MIN_CANDIDATE_POPULARITY = 0.0
MIN_CANDIDATE_RUNTIME = 1
MAX_CANDIDATE_GENRE_COUNT = 8
ALLOWED_CANDIDATE_STATUSES = {"개봉", "Released"}
PASSED_ACTION_WEIGHT = -5.0
PASSED_FEATURE_SIGNAL_NORMALIZER = 8.0
MAX_NEGATIVE_PENALTY_RATIO = 0.3
NEGATIVE_CANDIDATE_OVERSAMPLE_FACTOR = 4
FALLBACK_NEGATIVE_PENALTY_SCALE = 0.1
NEGATIVE_FEATURE_TYPE_WEIGHTS = {
    "genre": 0.2,
    "keyword": 0.35,
    "actor": 0.15,
    "director": 0.4,
    "theme": 0.45,
    "mood": 0.3,
}

POPULARITY_NORMALIZATION_DIVISOR = 100.0
MAX_NORMALIZED_POPULARITY = 2.0
RATING_NORMALIZATION_DIVISOR = 10.0
SUBSCRIBED_OTT_SCORE_BONUS = 0.15
MAX_QUALITY_BONUS_RATIO = 0.2
MAX_SESSION_SCORE_RATIO = 0.35

SESSION_TTL_SECONDS = 60 * 60 * 24
SESSION_EVENT_LIMIT = 100

DEFAULT_SCORE_CONFIG = {
    "profile": {
        "preferred_genre_weight": PREFERRED_GENRE_WEIGHT,
        "action_feature_weights": PROFILE_ACTION_FEATURE_WEIGHTS,
        "feature_limits": PROFILE_FEATURE_LIMITS,
    },
    "graph": {
        "direct_relation_weights": DIRECT_RELATION_WEIGHTS,
        "enable_overview_derivation": ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD,
        "enable_actor_nodes": ENABLE_ACTOR_NODES_IN_GRAPH_BUILD,
        "enable_actor_edges": ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD,
        "build_batch_size": GRAPH_BUILD_BATCH_SIZE,
    },
    "negative": {
        "passed": PASSED_ACTION_WEIGHT,
        "passed_feature_signal_normalizer": PASSED_FEATURE_SIGNAL_NORMALIZER,
        "feature_type_weights": NEGATIVE_FEATURE_TYPE_WEIGHTS,
        "max_penalty_ratio": MAX_NEGATIVE_PENALTY_RATIO,
        "candidate_oversample_factor": NEGATIVE_CANDIDATE_OVERSAMPLE_FACTOR,
        "fallback_penalty_scale": FALLBACK_NEGATIVE_PENALTY_SCALE,
    },
    "normalization": {
        "popularity_divisor": POPULARITY_NORMALIZATION_DIVISOR,
        "max_popularity": MAX_NORMALIZED_POPULARITY,
        "rating_divisor": RATING_NORMALIZATION_DIVISOR,
    },
    "bonuses": {
        "subscribed_ott": SUBSCRIBED_OTT_SCORE_BONUS,
    },
    "thresholds": {
        "minimum_relevance": 0.0,
        "max_quality_bonus_ratio": MAX_QUALITY_BONUS_RATIO,
        "max_session_score_ratio": MAX_SESSION_SCORE_RATIO,
        "max_negative_penalty_ratio": MAX_NEGATIVE_PENALTY_RATIO,
    },
    "candidate": {
        "slice_size": DEFAULT_CANDIDATE_SLICE_SIZE,
        "page_size": DEFAULT_PAGE_SIZE,
        "max_page_size": MAX_PAGE_SIZE,
        "quality": {
            "min_vote_count": MIN_CANDIDATE_VOTE_COUNT,
            "min_popularity": MIN_CANDIDATE_POPULARITY,
            "min_runtime": MIN_CANDIDATE_RUNTIME,
            "max_genre_count": MAX_CANDIDATE_GENRE_COUNT,
            "allowed_statuses": sorted(ALLOWED_CANDIDATE_STATUSES),
        },
    },
}

FEED_EVENT_THRESHOLDS = {
    "exposed_min_visible_ratio": 0.5,
    "exposed_min_ms": 500,
    "skipped_max_ms": 1500,
    "short_dwell_min_ms": 1500,
    "short_dwell_max_ms": 5000,
    "long_dwell_min_ms": 5000,
    "long_dwell_min_ratio": 0.6,
    "watched_min_ratio": 0.8,
}
