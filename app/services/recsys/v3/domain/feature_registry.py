from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


FEATURE_REGISTRY_VERSION = "v3.0.0"


class FeatureName(StrEnum):
    USER_IDENTITY = "user_identity"
    MOVIE_IDENTITY = "movie_identity"
    GENRE = "genre"
    KEYWORD = "keyword"
    ACTOR = "actor"
    DIRECTOR = "director"
    THEME = "theme"
    MOOD = "mood"
    OTT_STREAMING = "ott_streaming"
    OTT_RENT = "ott_rent"
    OTT_BUY = "ott_buy"


class FeatureFamily(StrEnum):
    IDENTITY = "identity"
    FACTUAL = "factual"
    SEMANTIC = "semantic"
    AVAILABILITY = "availability"


class FeatureValueType(StrEnum):
    INTEGER_ID = "integer_id"
    STRING_KEY = "string_key"


class FeatureConsumer(StrEnum):
    LIGHTFM_ITEM = "lightfm_item"
    LIGHTFM_USER = "lightfm_user"
    ONBOARDING_PROFILE = "onboarding_profile"
    LONG_TERM_PROFILE = "long_term_profile"
    SHORT_TERM_PROFILE = "short_term_profile"
    SERVING_CONTEXT = "serving_context"
    ONTOLOGY_EVIDENCE = "ontology_evidence"
    POLICY = "policy"
    EXPLANATION = "explanation"


class ConsumerStatus(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    DISABLED = "disabled"


class SourceReadiness(StrEnum):
    AVAILABLE = "available"
    PENDING_V3_ONTOLOGY = "pending_v3_ontology"
    REFERENCE_ONLY = "reference_only"


@dataclass(frozen=True, slots=True)
class FeatureSource:
    source_id: str
    readiness: SourceReadiness
    description: str


@dataclass(frozen=True, slots=True)
class ConsumerBinding:
    consumer: FeatureConsumer
    status: ConsumerStatus


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: FeatureName
    namespace: str
    family: FeatureFamily
    value_type: FeatureValueType
    sources: tuple[FeatureSource, ...]
    consumers: tuple[ConsumerBinding, ...]
    ontology_node_type: str | None = None
    ontology_relations: tuple[str, ...] = ()
    notes: str = ""

    def consumer_status(self, consumer: FeatureConsumer) -> ConsumerStatus:
        for binding in self.consumers:
            if binding.consumer == consumer:
                return binding.status
        raise KeyError(f"consumer is not registered feature={self.name.value} consumer={consumer.value}")

    def token(self, ref_id: int | str) -> str:
        normalized_ref_id = str(ref_id).strip()
        if not normalized_ref_id or ":" in normalized_ref_id:
            raise ValueError(f"invalid feature ref_id feature={self.name.value}")
        return f"{self.namespace}:{normalized_ref_id}"


def source(
    source_id: str,
    readiness: SourceReadiness,
    description: str,
) -> FeatureSource:
    return FeatureSource(
        source_id=source_id,
        readiness=readiness,
        description=description,
    )


def consumer_bindings(
    overrides: dict[FeatureConsumer, ConsumerStatus],
) -> tuple[ConsumerBinding, ...]:
    return tuple(
        ConsumerBinding(
            consumer=consumer,
            status=overrides.get(consumer, ConsumerStatus.DISABLED),
        )
        for consumer in FeatureConsumer
    )


AVAILABLE = SourceReadiness.AVAILABLE
PENDING = SourceReadiness.PENDING_V3_ONTOLOGY
REFERENCE = SourceReadiness.REFERENCE_ONLY
REQUIRED = ConsumerStatus.REQUIRED
OPTIONAL = ConsumerStatus.OPTIONAL


FEATURE_REGISTRY = (
    FeatureDefinition(
        name=FeatureName.USER_IDENTITY,
        namespace="user",
        family=FeatureFamily.IDENTITY,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(source("users.id", AVAILABLE, "Stable user identity for LightFM."),),
        consumers=consumer_bindings({FeatureConsumer.LIGHTFM_USER: REQUIRED}),
    ),
    FeatureDefinition(
        name=FeatureName.MOVIE_IDENTITY,
        namespace="movie",
        family=FeatureFamily.IDENTITY,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(source("movies.id", AVAILABLE, "Stable movie identity for LightFM."),),
        consumers=consumer_bindings({FeatureConsumer.LIGHTFM_ITEM: REQUIRED}),
        ontology_node_type="movie",
    ),
    FeatureDefinition(
        name=FeatureName.GENRE,
        namespace="genre",
        family=FeatureFamily.FACTUAL,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_genres.genre_id", AVAILABLE, "Movie-to-genre mapping."),
            source("user_genres.genre_id", AVAILABLE, "Onboarding genre selection."),
            source("v3_ontology.has_genre", PENDING, "V3 immutable graph relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: REQUIRED,
                FeatureConsumer.ONBOARDING_PROFILE: REQUIRED,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="genre",
        ontology_relations=("has_genre",),
    ),
    FeatureDefinition(
        name=FeatureName.KEYWORD,
        namespace="keyword",
        family=FeatureFamily.FACTUAL,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_keywords.keyword_id", AVAILABLE, "Movie-to-keyword mapping."),
            source("v3_ontology.has_keyword", PENDING, "V3 immutable graph relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: OPTIONAL,
                FeatureConsumer.ONBOARDING_PROFILE: OPTIONAL,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="keyword",
        ontology_relations=("has_keyword",),
    ),
    FeatureDefinition(
        name=FeatureName.ACTOR,
        namespace="actor",
        family=FeatureFamily.FACTUAL,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_actors.actor_id", AVAILABLE, "Movie cast mapping."),
            source("v3_ontology.has_actor", PENDING, "Person node with actor role relation."),
            source("v2_ontology.actor", REFERENCE, "V2 graph is audit/reference input only."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: OPTIONAL,
                FeatureConsumer.ONBOARDING_PROFILE: OPTIONAL,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="person",
        ontology_relations=("has_actor",),
        notes="Actor frequency pruning applies only in the LightFM exporter, not in the graph.",
    ),
    FeatureDefinition(
        name=FeatureName.DIRECTOR,
        namespace="director",
        family=FeatureFamily.FACTUAL,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_directors.director_id", AVAILABLE, "Movie director mapping."),
            source("v3_ontology.has_director", PENDING, "Person node with director role relation."),
            source("v2_ontology.director", REFERENCE, "V2 graph is audit/reference input only."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: OPTIONAL,
                FeatureConsumer.ONBOARDING_PROFILE: OPTIONAL,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="person",
        ontology_relations=("has_director",),
    ),
    FeatureDefinition(
        name=FeatureName.THEME,
        namespace="theme",
        family=FeatureFamily.SEMANTIC,
        value_type=FeatureValueType.STRING_KEY,
        sources=(
            source("movie_overview_semantic_signals.theme", AVAILABLE, "Extracted overview signal input."),
            source("assets/ontology/*.json", REFERENCE, "V2 assets require V3 versioned conversion."),
            source("v3_ontology.has_theme", PENDING, "Canonical V3 movie theme relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: OPTIONAL,
                FeatureConsumer.ONBOARDING_PROFILE: OPTIONAL,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="theme",
        ontology_relations=("has_theme",),
    ),
    FeatureDefinition(
        name=FeatureName.MOOD,
        namespace="mood",
        family=FeatureFamily.SEMANTIC,
        value_type=FeatureValueType.STRING_KEY,
        sources=(
            source("movie_overview_semantic_signals.mood", AVAILABLE, "Extracted overview signal input."),
            source("assets/ontology/*.json", REFERENCE, "V2 assets require V3 versioned conversion."),
            source("v3_ontology.has_mood", PENDING, "Canonical V3 movie mood relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.LIGHTFM_ITEM: REQUIRED,
                FeatureConsumer.LIGHTFM_USER: OPTIONAL,
                FeatureConsumer.ONBOARDING_PROFILE: OPTIONAL,
                FeatureConsumer.LONG_TERM_PROFILE: REQUIRED,
                FeatureConsumer.SHORT_TERM_PROFILE: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="mood",
        ontology_relations=("has_mood",),
    ),
    FeatureDefinition(
        name=FeatureName.OTT_STREAMING,
        namespace="ott_streaming",
        family=FeatureFamily.AVAILABILITY,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_otts.ott_id[is_streaming]", AVAILABLE, "Current movie streaming availability."),
            source("user_otts.ott_id", AVAILABLE, "Current user OTT subscription."),
            source("v3_ontology.available_streaming_on", PENDING, "V3 streaming relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.SERVING_CONTEXT: REQUIRED,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: REQUIRED,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="ott",
        ontology_relations=("available_streaming_on",),
        notes="OTT is a current availability rule input, not a V3 LightFM preference feature.",
    ),
    FeatureDefinition(
        name=FeatureName.OTT_RENT,
        namespace="ott_rent",
        family=FeatureFamily.AVAILABILITY,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_otts.ott_id[is_rent]", AVAILABLE, "Current movie rental availability."),
            source("v3_ontology.available_rent_on", PENDING, "V3 rental relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.SERVING_CONTEXT: OPTIONAL,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: OPTIONAL,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="ott",
        ontology_relations=("available_rent_on",),
    ),
    FeatureDefinition(
        name=FeatureName.OTT_BUY,
        namespace="ott_buy",
        family=FeatureFamily.AVAILABILITY,
        value_type=FeatureValueType.INTEGER_ID,
        sources=(
            source("movie_otts.ott_id[is_buy]", AVAILABLE, "Current movie purchase availability."),
            source("v3_ontology.available_buy_on", PENDING, "V3 purchase relation."),
        ),
        consumers=consumer_bindings(
            {
                FeatureConsumer.SERVING_CONTEXT: OPTIONAL,
                FeatureConsumer.ONTOLOGY_EVIDENCE: REQUIRED,
                FeatureConsumer.POLICY: OPTIONAL,
                FeatureConsumer.EXPLANATION: REQUIRED,
            }
        ),
        ontology_node_type="ott",
        ontology_relations=("available_buy_on",),
    ),
)


_FEATURE_BY_NAME = {definition.name: definition for definition in FEATURE_REGISTRY}


def get_feature_definition(name: FeatureName | str) -> FeatureDefinition:
    feature_name = name if isinstance(name, FeatureName) else FeatureName(name)
    try:
        return _FEATURE_BY_NAME[feature_name]
    except KeyError as exc:
        raise KeyError(f"unknown V3 feature name={feature_name.value}") from exc


def features_for_consumer(
    consumer: FeatureConsumer,
    *,
    include_optional: bool = False,
) -> tuple[FeatureDefinition, ...]:
    allowed_statuses = {ConsumerStatus.REQUIRED}
    if include_optional:
        allowed_statuses.add(ConsumerStatus.OPTIONAL)
    return tuple(
        definition
        for definition in FEATURE_REGISTRY
        if definition.consumer_status(consumer) in allowed_statuses
    )


def validate_feature_registry() -> None:
    if len(_FEATURE_BY_NAME) != len(FEATURE_REGISTRY):
        raise ValueError("duplicate V3 feature name")

    namespaces = [definition.namespace for definition in FEATURE_REGISTRY]
    if len(set(namespaces)) != len(namespaces):
        raise ValueError("duplicate V3 feature namespace")

    expected_consumers = set(FeatureConsumer)
    for definition in FEATURE_REGISTRY:
        if not definition.sources:
            raise ValueError(f"feature source is required feature={definition.name.value}")
        source_ids = [item.source_id for item in definition.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError(f"duplicate feature source feature={definition.name.value}")
        if any(not item.source_id.strip() or not item.description.strip() for item in definition.sources):
            raise ValueError(f"invalid feature source feature={definition.name.value}")
        if not definition.namespace or ":" in definition.namespace:
            raise ValueError(f"invalid feature namespace feature={definition.name.value}")
        registered_consumers = {binding.consumer for binding in definition.consumers}
        if registered_consumers != expected_consumers:
            raise ValueError(f"incomplete consumer bindings feature={definition.name.value}")
        if len(registered_consumers) != len(definition.consumers):
            raise ValueError(f"duplicate consumer binding feature={definition.name.value}")
        if definition.consumer_status(FeatureConsumer.ONTOLOGY_EVIDENCE) != ConsumerStatus.DISABLED:
            if not definition.ontology_node_type or not definition.ontology_relations:
                raise ValueError(f"ontology feature is missing graph mapping feature={definition.name.value}")


validate_feature_registry()
