from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


ONTOLOGY_ENGINE_NAME = "v3"
ONTOLOGY_SCHEMA_VERSION = "v3.0"
RELATION_REGISTRY_VERSION = "v3.0.0"


class NodeType(StrEnum):
    MOVIE = "movie"
    GENRE = "genre"
    KEYWORD = "keyword"
    PERSON = "person"
    THEME = "theme"
    MOOD = "mood"
    OTT = "ott"


class RelationCategory(StrEnum):
    FACTUAL = "factual"
    SEMANTIC_DERIVATION = "semantic_derivation"
    CANONICAL_SEMANTIC = "canonical_semantic"
    CONCEPT = "concept"


class RelationConsumer(StrEnum):
    FEATURE_EXPORTER = "feature_exporter"
    PROFILE = "profile"
    BUILD_DERIVATION = "build_derivation"
    ONTOLOGY_ANALYZER = "ontology_analyzer"
    POLICY = "policy"
    EVIDENCE = "evidence"
    EXPLANATION = "explanation"


@dataclass(frozen=True, slots=True)
class RelationDefinition:
    relation_type: str
    source_type: NodeType
    target_type: NodeType
    category: RelationCategory
    consumers: tuple[RelationConsumer, ...]
    active: bool = True
    symmetric: bool = False
    inverse_relation: str | None = None
    max_hops: int = 1
    requires_evidence: bool = False


RELATION_REGISTRY = (
    RelationDefinition(
        "has_genre",
        NodeType.MOVIE,
        NodeType.GENRE,
        RelationCategory.FACTUAL,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "has_keyword",
        NodeType.MOVIE,
        NodeType.KEYWORD,
        RelationCategory.FACTUAL,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "has_actor",
        NodeType.MOVIE,
        NodeType.PERSON,
        RelationCategory.FACTUAL,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "has_director",
        NodeType.MOVIE,
        NodeType.PERSON,
        RelationCategory.FACTUAL,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "available_streaming_on",
        NodeType.MOVIE,
        NodeType.OTT,
        RelationCategory.FACTUAL,
        (
            RelationConsumer.POLICY,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "available_rent_on",
        NodeType.MOVIE,
        NodeType.OTT,
        RelationCategory.FACTUAL,
        (RelationConsumer.EVIDENCE, RelationConsumer.EXPLANATION),
    ),
    RelationDefinition(
        "available_buy_on",
        NodeType.MOVIE,
        NodeType.OTT,
        RelationCategory.FACTUAL,
        (RelationConsumer.EVIDENCE, RelationConsumer.EXPLANATION),
    ),
    RelationDefinition(
        "suggests_theme",
        NodeType.GENRE,
        NodeType.THEME,
        RelationCategory.SEMANTIC_DERIVATION,
        (RelationConsumer.BUILD_DERIVATION,),
    ),
    RelationDefinition(
        "suggests_mood",
        NodeType.GENRE,
        NodeType.MOOD,
        RelationCategory.SEMANTIC_DERIVATION,
        (RelationConsumer.BUILD_DERIVATION,),
    ),
    RelationDefinition(
        "suggests_theme",
        NodeType.KEYWORD,
        NodeType.THEME,
        RelationCategory.SEMANTIC_DERIVATION,
        (RelationConsumer.BUILD_DERIVATION,),
    ),
    RelationDefinition(
        "suggests_mood",
        NodeType.KEYWORD,
        NodeType.MOOD,
        RelationCategory.SEMANTIC_DERIVATION,
        (RelationConsumer.BUILD_DERIVATION,),
    ),
    RelationDefinition(
        "has_theme",
        NodeType.MOVIE,
        NodeType.THEME,
        RelationCategory.CANONICAL_SEMANTIC,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.ONTOLOGY_ANALYZER,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
        requires_evidence=True,
    ),
    RelationDefinition(
        "has_mood",
        NodeType.MOVIE,
        NodeType.MOOD,
        RelationCategory.CANONICAL_SEMANTIC,
        (
            RelationConsumer.FEATURE_EXPORTER,
            RelationConsumer.PROFILE,
            RelationConsumer.ONTOLOGY_ANALYZER,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
        requires_evidence=True,
    ),
    RelationDefinition(
        "related_to",
        NodeType.THEME,
        NodeType.THEME,
        RelationCategory.CONCEPT,
        (RelationConsumer.ONTOLOGY_ANALYZER, RelationConsumer.EXPLANATION),
        symmetric=True,
    ),
    RelationDefinition(
        "broader_than",
        NodeType.THEME,
        NodeType.THEME,
        RelationCategory.CONCEPT,
        (RelationConsumer.ONTOLOGY_ANALYZER, RelationConsumer.EXPLANATION),
        active=False,
        inverse_relation="narrower_than",
    ),
    RelationDefinition(
        "narrower_than",
        NodeType.THEME,
        NodeType.THEME,
        RelationCategory.CONCEPT,
        (RelationConsumer.ONTOLOGY_ANALYZER, RelationConsumer.EXPLANATION),
        active=False,
        inverse_relation="broader_than",
    ),
    RelationDefinition(
        "evokes_mood",
        NodeType.THEME,
        NodeType.MOOD,
        RelationCategory.SEMANTIC_DERIVATION,
        (
            RelationConsumer.BUILD_DERIVATION,
            RelationConsumer.ONTOLOGY_ANALYZER,
            RelationConsumer.EVIDENCE,
            RelationConsumer.EXPLANATION,
        ),
    ),
    RelationDefinition(
        "compatible_with",
        NodeType.MOOD,
        NodeType.MOOD,
        RelationCategory.CONCEPT,
        (RelationConsumer.ONTOLOGY_ANALYZER,),
        symmetric=True,
    ),
)


_RELATION_BY_KEY = {
    (
        definition.relation_type,
        definition.source_type,
        definition.target_type,
    ): definition
    for definition in RELATION_REGISTRY
}


def get_relation_definition(
    relation_type: str,
    *,
    source_type: NodeType | str | None = None,
    target_type: NodeType | str | None = None,
) -> RelationDefinition:
    if source_type is not None and target_type is not None:
        normalized_source = source_type if isinstance(source_type, NodeType) else NodeType(source_type)
        normalized_target = target_type if isinstance(target_type, NodeType) else NodeType(target_type)
        try:
            return _RELATION_BY_KEY[(relation_type, normalized_source, normalized_target)]
        except KeyError as exc:
            raise KeyError(
                f"unknown V3 ontology relation={relation_type!r} "
                f"source={normalized_source.value} target={normalized_target.value}"
            ) from exc

    matches = tuple(
        definition
        for definition in RELATION_REGISTRY
        if definition.relation_type == relation_type
    )
    if not matches:
        raise KeyError(f"unknown V3 ontology relation={relation_type!r}")
    if len(matches) > 1:
        raise ValueError(f"relation endpoints are required relation={relation_type!r}")
    return matches[0]


def relations_for_consumer(
    consumer: RelationConsumer,
    *,
    active_only: bool = True,
) -> tuple[RelationDefinition, ...]:
    return tuple(
        definition
        for definition in RELATION_REGISTRY
        if consumer in definition.consumers and (definition.active or not active_only)
    )


def validate_edge_contract(
    *,
    relation_type: str,
    source_type: str,
    target_type: str,
    weight: float,
    confidence: float,
    effective_strength: float | None,
    evidence_count: int,
) -> None:
    definition = get_relation_definition(
        relation_type,
        source_type=source_type,
        target_type=target_type,
    )
    if source_type != definition.source_type.value or target_type != definition.target_type.value:
        raise ValueError(
            f"invalid relation endpoints relation={relation_type} "
            f"source={source_type} target={target_type}"
        )
    for name, value in (("weight", weight), ("confidence", confidence)):
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"ontology edge {name} must be between 0 and 1")
    if effective_strength is not None:
        if not math.isfinite(effective_strength) or not 0.0 <= effective_strength <= 1.0:
            raise ValueError("ontology edge effective_strength must be between 0 and 1")
    if evidence_count < 0:
        raise ValueError("ontology edge evidence_count cannot be negative")
    if definition.requires_evidence and evidence_count == 0:
        raise ValueError(f"canonical relation requires evidence relation={relation_type}")


def validate_relation_registry() -> None:
    if len(_RELATION_BY_KEY) != len(RELATION_REGISTRY):
        raise ValueError("duplicate V3 ontology relation endpoint contract")

    for definition in RELATION_REGISTRY:
        if not definition.relation_type.strip():
            raise ValueError("ontology relation type cannot be empty")
        if not definition.consumers:
            raise ValueError(f"ontology relation has no consumer relation={definition.relation_type}")
        if definition.max_hops != 1:
            raise ValueError(f"V3 relation max_hops must be one relation={definition.relation_type}")
        if definition.symmetric and definition.source_type != definition.target_type:
            raise ValueError(f"symmetric relation endpoints must match relation={definition.relation_type}")
        if definition.inverse_relation:
            inverse_matches = tuple(
                candidate
                for candidate in RELATION_REGISTRY
                if candidate.relation_type == definition.inverse_relation
                and candidate.source_type == definition.target_type
                and candidate.target_type == definition.source_type
            )
            if len(inverse_matches) != 1 or inverse_matches[0].inverse_relation != definition.relation_type:
                raise ValueError(f"invalid inverse relation pair relation={definition.relation_type}")
            inverse = inverse_matches[0]
            if inverse.source_type != definition.target_type or inverse.target_type != definition.source_type:
                raise ValueError(f"inverse relation endpoints do not match relation={definition.relation_type}")

    ott_feature_relations = {
        "available_streaming_on",
        "available_rent_on",
        "available_buy_on",
    }
    exported_relations = {
        definition.relation_type
        for definition in relations_for_consumer(RelationConsumer.FEATURE_EXPORTER)
    }
    if ott_feature_relations & exported_relations:
        raise ValueError("OTT relations cannot be exported as V3 LightFM features")


validate_relation_registry()
