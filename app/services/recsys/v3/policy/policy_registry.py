from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PolicySource(StrEnum):
    V1 = "v1"
    V2 = "v2"
    V3_NEW = "v3_new"


class DecisionStatus(StrEnum):
    ADOPTED = "adopted"
    PROVISIONAL = "provisional"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    policy_id: str
    selected_source: PolicySource
    status: DecisionStatus
    selection_reason: str
    references: tuple[str, ...]
    config_keys: tuple[str, ...] = ()
    comparison_required: bool = False


POLICY_REGISTRY = (
    PolicyDecision(
        policy_id="training_positive_actions",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Keep V1 direct behavior semantics, add onboarding favorite, and express relative strength "
            "as LightFM sample weight instead of copying V1 scores."
        ),
        references=(
            "app/jobs/recsys/v1/worker.py",
            "app/services/recsys/v2/profile_builder.py",
        ),
        config_keys=("TRAINING_ACTION_WEIGHTS",),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="training_social_engagement_sources",
        selected_source=PolicySource.V1,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Retain V1's movie/playlist post, post-like, and reply sources, while treating them as "
            "lower-confidence LightFM interactions with explicit provenance and caps."
        ),
        references=(
            "app/jobs/recsys/v1/worker.py",
            "z_v3_docs/03_recommendation_policy.md",
            "z_v3_docs/04_lightfm_tuning.md",
        ),
        config_keys=(
            "TRAINING_SOCIAL_ACTION_WEIGHTS",
            "TRAINING_SOCIAL_ONLY_MAX_WEIGHT",
            "TRAINING_SOCIAL_BONUS_CAP",
            "TRAINING_SOCIAL_MISSING_TIMESTAMP_MULTIPLIER",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="training_playlist_signal_projection",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Conserve each playlist-derived event with 1/N movie units and restrict membership to the "
            "action timestamp; defer playlist-post likes because likes currently have no timestamp."
        ),
        references=(
            "app/jobs/recsys/v1/worker.py",
            "app/models/mapping.py",
            "z_v3_docs/03_recommendation_policy.md",
        ),
        config_keys=("TRAINING_PLAYLIST_DERIVED_MULTIPLIER",),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="training_passed_handling",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.ADOPTED,
        selection_reason=(
            "Preserve V1 exclusion meaning while keeping passed out of WARP positive interactions; "
            "semantic negative scoring remains a separate policy."
        ),
        references=(
            "app/jobs/recsys/v1/worker.py",
            "app/services/recsys/v2/candidate_generator.py",
        ),
    ),
    PolicyDecision(
        policy_id="training_recency_decay",
        selected_source=PolicySource.V1,
        status=DecisionStatus.PROVISIONAL,
        selection_reason="Reuse V1's interpretable 30/90/180-day buckets as the initial sample-weight decay.",
        references=("app/jobs/recsys/v1/worker.py",),
        config_keys=(
            "TRAINING_RECENCY_BUCKETS",
            "TRAINING_OLDER_RECENCY_MULTIPLIER",
            "TRAINING_MISSING_TIMESTAMP_MULTIPLIER",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="training_positive_overlap",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Use the strongest action plus a bounded confidence bonus so overlapping snapshot states "
            "cannot inflate one user-movie pair linearly."
        ),
        references=("z_v3_docs/04_lightfm_tuning.md",),
        config_keys=(
            "TRAINING_OVERLAP_CONFIDENCE_BONUS",
            "TRAINING_MAX_SAMPLE_WEIGHT",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_watched_passed_exclusion",
        selected_source=PolicySource.V1,
        status=DecisionStatus.ADOPTED,
        selection_reason="Watched and passed movies must not be shown again to the same user.",
        references=(
            "app/jobs/recsys/v1/worker.py",
            "app/services/recsys/v1/recommendation.py",
        ),
    ),
    PolicyDecision(
        policy_id="serving_subscribed_only_filter",
        selected_source=PolicySource.V1,
        status=DecisionStatus.ADOPTED,
        selection_reason="Subscribed-only mode is a hard OTT filter and cannot fall back to the full catalog.",
        references=("app/services/recsys/v1/recommendation.py",),
    ),
    PolicyDecision(
        policy_id="ontology_evidence_query",
        selected_source=PolicySource.V2,
        status=DecisionStatus.ADOPTED,
        selection_reason="V2 provides the set-based, bounded graph-query pattern absent from V1.",
        references=("app/services/recsys/v2/candidate_generator.py",),
    ),
    PolicyDecision(
        policy_id="ontology_semantic_negative",
        selected_source=PolicySource.V2,
        status=DecisionStatus.PROVISIONAL,
        selection_reason="V2 supplies bounded feature-level negative evidence, but its absolute weights are not inherited.",
        references=("app/services/recsys/v2/candidate_generator.py",),
        config_keys=(
            "POLICY_NEGATIVE_FEATURE_WEIGHTS",
            "POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER",
            "POLICY_NEGATIVE_CONFIDENCE_PAIR_COUNT",
            "POLICY_NEGATIVE_MAX_BASE_RATIO",
            "POLICY_NEGATIVE_MAX_ABSOLUTE",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_hard_eligibility",
        selected_source=PolicySource.V1,
        status=DecisionStatus.ADOPTED,
        selection_reason=(
            "Retain V1 watched/passed and OTT eligibility, and add explicit session, adult, title, "
            "movie block, and canceled-status boundaries without a minimum-vote hard filter."
        ),
        references=(
            "app/services/recsys/v1/recommendation.py",
            "z_v3_docs/03_recommendation_policy.md",
        ),
        config_keys=("POLICY_BLOCKED_MOVIE_STATUSES",),
    ),
    PolicyDecision(
        policy_id="serving_score_composition",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Keep normalized candidate selection, ontology evidence, and policy effects as separate "
            "components so ontology reasons are not presented as LightFM attribution."
        ),
        references=("z_v3_docs/03_recommendation_policy.md",),
        config_keys=(
            "POLICY_PERSONAL_COMPONENT_WEIGHT",
            "POLICY_ONTOLOGY_COMPONENT_WEIGHT",
            "POLICY_ONTOLOGY_SHORT_TERM_MULTIPLIER",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_ott_adjustment",
        selected_source=PolicySource.V1,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Use current streaming availability as a hard subscribed-only filter and retain only a "
            "small additive subscribed-service bonus in all mode."
        ),
        references=("app/services/recsys/v1/recommendation.py",),
        config_keys=("POLICY_OTT_BONUS_MAX",),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_quality_adjustment",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Apply vote-count confidence before rating and popularity so one vote or popularity alone "
            "cannot dominate, while avoiding a minimum-vote hard filter."
        ),
        references=("z_v3_docs/03_recommendation_policy.md",),
        config_keys=(
            "POLICY_QUALITY_BONUS_MAX",
            "POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR",
            "POLICY_QUALITY_POPULARITY_REFERENCE",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_deterministic_mmr",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Use bounded genre/actor/director/theme/mood repetition and similarity penalties without "
            "random replacement, keeping relevance as the dominant score."
        ),
        references=("z_v3_docs/03_recommendation_policy.md",),
        config_keys=(
            "POLICY_MMR_SIMILARITY_PENALTY_MAX",
            "POLICY_REPETITION_PENALTY_MAX",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="serving_cold_start",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Keep onboarding ontology rules dominant over feature-only LightFM, preserve onboarding "
            "evidence through final analysis, require overview support for genre-only semantic expansion, "
            "exclude onboarding favorites, rank trusted genre-only candidates by separated semantic and "
            "quality scores, and mark graph candidates absent from the model as ontology cold items."
        ),
        references=(
            "z_v3_docs/03_recommendation_policy.md",
            "app/services/recsys/v1/dynamic_retriever.py",
        ),
        config_keys=(
            "COLD_START_FEATURE_ONLY_MODEL_WEIGHT",
            "COLD_START_GENRE_ONLY_MODEL_WEIGHT",
            "COLD_START_OVERVIEW_SUPPORT_BONUS_MAX",
            "COLD_START_GENRE_ONLY_SEMANTIC_WEIGHT",
            "COLD_START_GENRE_ONLY_QUALITY_WEIGHT",
            "COLD_START_GENRE_COVERAGE_WEIGHT",
            "COLD_START_GENRE_SPECIFICITY_WEIGHT",
            "COLD_START_GENRE_ONLY_MIN_VOTE_COUNT",
            "COLD_START_GENRE_ONLY_TRUSTED_VOTE_COUNT",
        ),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="candidate_retrieval",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.PROVISIONAL,
        selection_reason=(
            "Replace V1 user-cosine retrieval and V2 graph-only retrieval with hybrid "
            "LightFM top-150 storage: 100 active candidates plus 50 ordered reserves, "
            "with at most 100 candidates entering detailed analysis."
        ),
        references=(
            "app/jobs/recsys/v1/worker.py",
            "app/services/recsys/v2/candidate_generator.py",
        ),
        config_keys=("CANDIDATE_POOL_SIZE", "CANDIDATE_RESERVE_SIZE", "CANDIDATE_STORAGE_SIZE"),
        comparison_required=True,
    ),
    PolicyDecision(
        policy_id="random_exploration",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.DEFERRED,
        selection_reason="Defer random and long-tail injection until the accuracy baseline is stable.",
        references=("z_v3_docs/03_recommendation_policy.md",),
    ),
    PolicyDecision(
        policy_id="serving_bundle_activation",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.ADOPTED,
        selection_reason=(
            "Activate model, ontology, candidate snapshot, feature registry, and policy config through "
            "one immutable pointer; reject partial or incompatible activation."
        ),
        references=("z_v3_docs/02_implementation_guide.md",),
        config_keys=(
            "SERVING_BUNDLE_FORMAT_VERSION",
            "POLICY_CONFIG_VERSION",
        ),
    ),
    PolicyDecision(
        policy_id="serving_bundle_reload",
        selected_source=PolicySource.V3_NEW,
        status=DecisionStatus.ADOPTED,
        selection_reason=(
            "Cache a fully validated hybrid artifact in memory and retain the previous valid bundle "
            "when a changed pointer or artifact fails validation."
        ),
        references=("z_v3_docs/02_implementation_guide.md",),
        config_keys=("SERVING_BUNDLE_ROOT",),
    ),
)


def get_policy_decision(policy_id: str) -> PolicyDecision:
    try:
        return _POLICY_BY_ID[policy_id]
    except KeyError as exc:
        raise KeyError(f"unknown V3 policy_id={policy_id!r}") from exc


def validate_policy_registry() -> None:
    if len(_POLICY_BY_ID) != len(POLICY_REGISTRY):
        raise ValueError("duplicate V3 policy_id")
    for decision in POLICY_REGISTRY:
        if not decision.selection_reason.strip():
            raise ValueError(f"missing selection reason policy_id={decision.policy_id}")
        if not decision.references:
            raise ValueError(f"missing policy reference policy_id={decision.policy_id}")


_POLICY_BY_ID = {decision.policy_id: decision for decision in POLICY_REGISTRY}
validate_policy_registry()
