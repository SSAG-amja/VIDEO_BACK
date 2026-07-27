ENGINE_NAME = "ontology_v2"
ENGINE_VERSION = "v2.0.0"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
DEFAULT_CANDIDATE_SLICE_SIZE = 80
ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD = False
ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD = False
ENABLE_ACTOR_NODES_IN_GRAPH_BUILD = False
GRAPH_BUILD_BATCH_SIZE = 100_000

SESSION_TTL_SECONDS = 60 * 60 * 24
SESSION_EVENT_LIMIT = 100

DEFAULT_SCORE_CONFIG = {
    "weights": {
        "genre": 1.0,
        "keyword": 0.7,
        "actor": 0.5,
        "director": 0.9,
        "theme": 1.2,
        "mood": 0.8,
        "semantic_expansion": 0.6,
        "session_score": 1.0,
        "quality_bonus": 0.2,
        "ott_bonus": 0.1,
    },
    "action_weights": {
        "favorite_movie": 5.0,
        "preferred_genre": 4.0,
        "pinned": 4.0,
        "playlist_add": 3.0,
        "watched": 1.5,
        "exposed_only": -0.1,
        "skipped": -0.3,
        "short_dwell": -0.5,
        "long_dwell": 0.8,
        "post_write": 0.7,
        "like": 0.5,
        "reply": 0.3,
        "passed": -5.0,
    },
    "thresholds": {
        "minimum_relevance": 0.0,
        "max_quality_bonus_ratio": 0.2,
        "max_session_score_ratio": 0.35,
    },
    "candidate_slice_size": DEFAULT_CANDIDATE_SLICE_SIZE,
    "page_size": DEFAULT_PAGE_SIZE,
    "max_page_size": MAX_PAGE_SIZE,
    "enable_overview_derivation_in_graph_build": ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD,
    "enable_actor_nodes_in_graph_build": ENABLE_ACTOR_NODES_IN_GRAPH_BUILD,
    "enable_actor_edges_in_graph_build": ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD,
    "graph_build_batch_size": GRAPH_BUILD_BATCH_SIZE,
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
