from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, hstack, identity
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.features.feature_schemas import (
    ItemFeatureExport,
    ItemFeatureFamilyDiagnostics,
    ItemFeatureManifest,
    ItemFeaturePruningRule,
)
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.config import (
    ITEM_FEATURE_EXPORTER_VERSION,
    ITEM_FEATURE_MAX_CATALOG_RATIO,
    ITEM_FEATURE_MIN_MOVIE_FREQUENCY,
    ONTOLOGY_BUILD_WORK_MEM,
)
from app.services.recsys.v3.domain.feature_registry import (
    ConsumerStatus,
    FeatureConsumer,
    FeatureDefinition,
    FeatureName,
    FeatureValueType,
    features_for_consumer,
    get_feature_definition,
)
from app.services.recsys.v3.domain.ontology_registry import (
    ONTOLOGY_ENGINE_NAME,
    ONTOLOGY_SCHEMA_VERSION,
)
from app.services.recsys.v3.domain.schemas import FeatureCoverageDiagnostics, FeatureDropCount


def default_item_feature_pruning_rules() -> dict[FeatureName, ItemFeaturePruningRule]:
    return {
        definition.name: ItemFeaturePruningRule(
            min_movie_frequency=ITEM_FEATURE_MIN_MOVIE_FREQUENCY.get(
                definition.name.value,
                1,
            ),
            max_catalog_ratio=ITEM_FEATURE_MAX_CATALOG_RATIO.get(
                definition.name.value
            ),
        )
        for definition in graph_item_feature_definitions()
    }


def graph_item_feature_definitions() -> tuple[FeatureDefinition, ...]:
    definitions = features_for_consumer(
        FeatureConsumer.LIGHTFM_ITEM,
        include_optional=True,
    )
    graph_definitions = tuple(
        definition
        for definition in definitions
        if definition.name != FeatureName.MOVIE_IDENTITY
    )
    for definition in graph_definitions:
        if len(definition.ontology_relations) != 1 or definition.ontology_node_type is None:
            raise ValueError(
                "LightFM item graph feature requires one ontology relation "
                f"feature={definition.name.value}"
            )
    return graph_definitions


def export_item_features(
    db: Session,
    ontology_build_id: int,
    *,
    pruning_rules: Mapping[FeatureName, ItemFeaturePruningRule] | None = None,
    require_required_coverage: bool = True,
) -> ItemFeatureExport:
    build = _load_compatible_build(db, ontology_build_id)
    definitions = graph_item_feature_definitions()
    rules = default_item_feature_pruning_rules()
    if pruning_rules is not None:
        unknown_features = set(pruning_rules) - {item.name for item in definitions}
        if unknown_features:
            raise ValueError(
                "pruning rules contain non-item graph features "
                f"features={sorted(item.value for item in unknown_features)}"
            )
        rules.update(pruning_rules)

    db.execute(
        text("SELECT set_config('work_mem', :work_mem, true)"),
        {"work_mem": ONTOLOGY_BUILD_WORK_MEM},
    )
    db.execute(
        text("SELECT set_config('max_parallel_workers_per_gather', '0', true)")
    )
    movie_ids = _load_graph_movie_ids(db, ontology_build_id)
    movie_id_map = {movie_id: index for index, movie_id in enumerate(movie_ids)}
    identity_definition = get_feature_definition(FeatureName.MOVIE_IDENTITY)
    identity_tokens = tuple(identity_definition.token(movie_id) for movie_id in movie_ids)
    matrices: list[csr_matrix] = [
        identity(len(movie_ids), format="csr", dtype=np.float32)
    ]
    feature_tokens = list(identity_tokens)
    family_diagnostics: list[ItemFeatureFamilyDiagnostics] = []

    for definition in definitions:
        matrix, tokens, diagnostics = _export_graph_feature_family(
            db,
            ontology_build_id=ontology_build_id,
            definition=definition,
            rule=rules[definition.name],
            movie_ids=movie_ids,
            movie_id_map=movie_id_map,
        )
        if (
            require_required_coverage
            and definition.consumer_status(FeatureConsumer.LIGHTFM_ITEM)
            == ConsumerStatus.REQUIRED
            and (
                diagnostics.coverage.source_value_count == 0
                or diagnostics.coverage.retained_value_count == 0
                or diagnostics.coverage.covered_entity_count == 0
            )
        ):
            raise ValueError(
                "required LightFM item feature has no retained coverage "
                f"feature={definition.name.value} build_id={ontology_build_id}"
            )
        matrices.append(matrix)
        feature_tokens.extend(tokens)
        family_diagnostics.append(diagnostics)

    item_features = hstack(matrices, format="csr", dtype=np.float32)
    item_features.sum_duplicates()
    item_features.sort_indices()
    tokens = tuple(feature_tokens)
    feature_token_map = {token: index for index, token in enumerate(tokens)}
    if len(feature_token_map) != len(tokens):
        raise ValueError("duplicate LightFM item feature token")

    movie_mapping_hash = _hash_ordered_values("movie", (str(item) for item in movie_ids))
    feature_mapping_hash = _hash_ordered_values("feature", tokens)
    export_hash = _hash_export(
        build=build,
        movie_mapping_hash=movie_mapping_hash,
        feature_mapping_hash=feature_mapping_hash,
        matrix=item_features,
        pruning_rules=rules,
    )
    manifest = ItemFeatureManifest(
        exporter_version=ITEM_FEATURE_EXPORTER_VERSION,
        ontology_build_id=build.id,
        ontology_engine_name=build.engine_name,
        ontology_schema_version=build.schema_version,
        ontology_source_hash=build.source_hash,
        movie_count=len(movie_ids),
        feature_count=len(tokens),
        matrix_nnz=int(item_features.nnz),
        matrix_shape=(int(item_features.shape[0]), int(item_features.shape[1])),
        movie_mapping_hash=movie_mapping_hash,
        feature_mapping_hash=feature_mapping_hash,
        export_hash=export_hash,
        pruning_rules={
            feature.value: {
                "min_movie_frequency": rule.min_movie_frequency,
                "max_catalog_ratio": rule.max_catalog_ratio,
            }
            for feature, rule in sorted(rules.items(), key=lambda item: item[0].value)
        },
        family_diagnostics=tuple(family_diagnostics),
        ontology_build_status=build.status,
    )
    return ItemFeatureExport(
        movie_ids=movie_ids,
        movie_id_map=movie_id_map,
        feature_tokens=tokens,
        feature_token_map=feature_token_map,
        item_features=item_features,
        manifest=manifest,
    )


def _load_compatible_build(db: Session, ontology_build_id: int) -> OntologyBuild:
    if ontology_build_id <= 0:
        raise ValueError("ontology build ID must be positive")
    build = db.get(OntologyBuild, ontology_build_id)
    if build is None:
        raise ValueError(f"ontology build does not exist build_id={ontology_build_id}")
    if (
        build.engine_name != ONTOLOGY_ENGINE_NAME
        or build.schema_version != ONTOLOGY_SCHEMA_VERSION
    ):
        raise ValueError(
            "item feature exporter requires a V3 ontology build "
            f"build_id={ontology_build_id} engine={build.engine_name} "
            f"schema={build.schema_version}"
        )
    if build.status not in {"running", "success"}:
        raise ValueError(
            "item feature exporter requires a materialized build "
            f"build_id={ontology_build_id} status={build.status}"
        )
    return build


def _load_graph_movie_ids(db: Session, ontology_build_id: int) -> tuple[int, ...]:
    rows = db.execute(
        text(
            """
            SELECT ref_id
            FROM ontology_nodes
            WHERE build_id = :build_id AND node_type = 'movie'
            ORDER BY ref_id::bigint
            """
        ),
        {"build_id": ontology_build_id},
    ).scalars()
    movie_ids = tuple(int(ref_id) for ref_id in rows)
    if not movie_ids:
        raise ValueError(f"ontology build has no movie nodes build_id={ontology_build_id}")
    if any(movie_id <= 0 for movie_id in movie_ids) or len(set(movie_ids)) != len(movie_ids):
        raise ValueError("ontology movie ref IDs must be unique positive integers")
    return movie_ids


def _export_graph_feature_family(
    db: Session,
    *,
    ontology_build_id: int,
    definition: FeatureDefinition,
    rule: ItemFeaturePruningRule,
    movie_ids: tuple[int, ...],
    movie_id_map: dict[int, int],
) -> tuple[csr_matrix, tuple[str, ...], ItemFeatureFamilyDiagnostics]:
    relation_type = definition.ontology_relations[0]
    _validate_relation_endpoints(
        db,
        ontology_build_id=ontology_build_id,
        relation_type=relation_type,
        target_node_type=definition.ontology_node_type or "",
    )
    frequency_table = (
        f"v3_item_frequency_{ontology_build_id}_{definition.namespace}"
    )
    db.execute(text(f"DROP TABLE IF EXISTS pg_temp.{frequency_table}"))
    try:
        db.execute(
            text(
                f"""
                CREATE TEMP TABLE {frequency_table} ON COMMIT DROP AS
                SELECT edge.target_node_id,
                       target.ref_id,
                       count(*)::bigint AS movie_frequency
                FROM ontology_edges edge
                JOIN ontology_nodes source
                  ON source.id = edge.source_node_id
                 AND source.build_id = :build_id
                 AND source.node_type = 'movie'
                JOIN ontology_nodes target
                  ON target.id = edge.target_node_id
                 AND target.build_id = :build_id
                 AND target.node_type = :target_node_type
                WHERE edge.build_id = :build_id
                  AND edge.relation_type = :relation_type
                GROUP BY edge.target_node_id, target.ref_id
                """
            ),
            {
                "build_id": ontology_build_id,
                "relation_type": relation_type,
                "target_node_type": definition.ontology_node_type,
            },
        )
        db.execute(
            text(
                f"CREATE UNIQUE INDEX ON {frequency_table} (target_node_id)"
            )
        )
        retention_clause, params = _retention_clause(
            rule,
            movie_count=len(movie_ids),
        )
        high_frequency_clause = _high_frequency_drop_clause(rule)
        summary = db.execute(
            text(
                f"""
                SELECT count(*)::bigint AS source_value_count,
                       COALESCE(sum(movie_frequency), 0)::bigint AS source_edge_count,
                       count(*) FILTER (WHERE {retention_clause})::bigint
                           AS retained_value_count,
                       COALESCE(
                           sum(movie_frequency) FILTER (WHERE {retention_clause}),
                           0
                       )::bigint AS retained_edge_count,
                       count(*) FILTER (
                           WHERE movie_frequency < :min_movie_frequency
                       )::bigint AS low_frequency_count,
                       count(*) FILTER (WHERE {high_frequency_clause})::bigint
                           AS high_frequency_count
                FROM {frequency_table}
                """
            ),
            params,
        ).one()
        ref_order = (
            "ref_id::bigint"
            if definition.value_type == FeatureValueType.INTEGER_ID
            else "ref_id"
        )
        retained_refs = tuple(
            str(ref_id)
            for ref_id in db.execute(
                text(
                    f"""
                    SELECT ref_id
                    FROM {frequency_table}
                    WHERE {retention_clause}
                    ORDER BY {ref_order}
                    """
                ),
                params,
            ).scalars()
        )
        tokens = tuple(definition.token(ref_id) for ref_id in retained_refs)
        local_column_map = {
            ref_id: index for index, ref_id in enumerate(retained_refs)
        }
        retained_edge_count = int(summary.retained_edge_count)
        matrix, covered_movie_count = _load_family_matrix(
            db,
            ontology_build_id=ontology_build_id,
            definition=definition,
            relation_type=relation_type,
            frequency_table=frequency_table,
            retention_clause=retention_clause,
            params=params,
            movie_id_map=movie_id_map,
            local_column_map=local_column_map,
            retained_edge_count=retained_edge_count,
        )

        low_frequency_count = int(summary.low_frequency_count)
        high_frequency_count = int(summary.high_frequency_count)
        drop_counts = tuple(
            item
            for item in (
                FeatureDropCount("low_frequency", low_frequency_count),
                FeatureDropCount("high_catalog_ratio", high_frequency_count),
            )
            if item.count > 0
        )
        source_value_count = int(summary.source_value_count)
        retained_value_count = int(summary.retained_value_count)
        coverage = FeatureCoverageDiagnostics(
            feature=definition.name,
            consumer=FeatureConsumer.LIGHTFM_ITEM,
            total_entity_count=len(movie_ids),
            covered_entity_count=covered_movie_count,
            source_value_count=source_value_count,
            retained_value_count=retained_value_count,
            dropped_value_count=source_value_count - retained_value_count,
            drop_counts=drop_counts,
        )
        diagnostics = ItemFeatureFamilyDiagnostics(
            feature=definition.name,
            relation_type=relation_type,
            source_edge_count=int(summary.source_edge_count),
            retained_edge_count=retained_edge_count,
            matrix_nnz=int(matrix.nnz),
            coverage=coverage,
        )
        return matrix, tokens, diagnostics
    finally:
        db.execute(text(f"DROP TABLE IF EXISTS pg_temp.{frequency_table}"))


def _load_family_matrix(
    db: Session,
    *,
    ontology_build_id: int,
    definition: FeatureDefinition,
    relation_type: str,
    frequency_table: str,
    retention_clause: str,
    params: dict[str, int | float],
    movie_id_map: dict[int, int],
    local_column_map: dict[str, int],
    retained_edge_count: int,
) -> tuple[csr_matrix, int]:
    row_indices = np.empty(retained_edge_count, dtype=np.int32)
    column_indices = np.empty(retained_edge_count, dtype=np.int32)
    values = np.empty(retained_edge_count, dtype=np.float32)
    covered_rows = np.zeros(len(movie_id_map), dtype=np.bool_)
    target_order = (
        "target.ref_id::bigint"
        if definition.value_type == FeatureValueType.INTEGER_ID
        else "target.ref_id"
    )
    statement = text(
        f"""
        SELECT source.ref_id AS movie_ref_id,
               target.ref_id AS feature_ref_id,
               edge.effective_strength
        FROM ontology_edges edge
        JOIN ontology_nodes source
          ON source.id = edge.source_node_id
         AND source.build_id = :build_id
         AND source.node_type = 'movie'
        JOIN ontology_nodes target
          ON target.id = edge.target_node_id
         AND target.build_id = :build_id
         AND target.node_type = :target_node_type
        JOIN {frequency_table} frequency
          ON frequency.target_node_id = edge.target_node_id
        WHERE edge.build_id = :build_id
          AND edge.relation_type = :relation_type
          AND {retention_clause}
        ORDER BY source.ref_id::bigint, {target_order}
        """
    ).execution_options(stream_results=True, yield_per=10_000)
    query_params = {
        **params,
        "build_id": ontology_build_id,
        "target_node_type": definition.ontology_node_type,
        "relation_type": relation_type,
    }
    position = 0
    for movie_ref_id, feature_ref_id, effective_strength in db.execute(
        statement,
        query_params,
    ):
        if position >= retained_edge_count:
            raise ValueError("retained edge query exceeded its frequency summary")
        movie_id = int(movie_ref_id)
        try:
            row_index = movie_id_map[movie_id]
            column_index = local_column_map[str(feature_ref_id)]
        except KeyError as exc:
            raise ValueError(
                "feature edge references an unmapped movie or feature "
                f"feature={definition.name.value}"
            ) from exc
        value = float(effective_strength)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                "LightFM item feature strength must be finite and positive "
                f"feature={definition.name.value} value={value}"
            )
        row_indices[position] = row_index
        column_indices[position] = column_index
        values[position] = value
        covered_rows[row_index] = True
        position += 1
    if position != retained_edge_count:
        raise ValueError(
            "retained edge query count differs from frequency summary "
            f"feature={definition.name.value} expected={retained_edge_count} actual={position}"
        )
    matrix = coo_matrix(
        (values, (row_indices, column_indices)),
        shape=(len(movie_id_map), len(local_column_map)),
        dtype=np.float32,
    ).tocsr()
    matrix.sum_duplicates()
    matrix.sort_indices()
    if int(matrix.nnz) != retained_edge_count:
        raise ValueError(
            "duplicate movie-feature coordinates were exported "
            f"feature={definition.name.value}"
        )
    return matrix, int(np.count_nonzero(covered_rows))


def _validate_relation_endpoints(
    db: Session,
    *,
    ontology_build_id: int,
    relation_type: str,
    target_node_type: str,
) -> None:
    invalid_count = int(
        db.execute(
            text(
                """
                SELECT count(*)
                FROM ontology_edges edge
                JOIN ontology_nodes source ON source.id = edge.source_node_id
                JOIN ontology_nodes target ON target.id = edge.target_node_id
                WHERE edge.build_id = :build_id
                  AND edge.relation_type = :relation_type
                  AND (
                      source.build_id <> edge.build_id
                      OR target.build_id <> edge.build_id
                      OR source.node_type <> 'movie'
                      OR target.node_type <> :target_node_type
                  )
                """
            ),
            {
                "build_id": ontology_build_id,
                "relation_type": relation_type,
                "target_node_type": target_node_type,
            },
        ).scalar_one()
    )
    if invalid_count:
        raise ValueError(
            "ontology relation endpoint mismatch during item feature export "
            f"relation={relation_type} count={invalid_count}"
        )


def _retention_clause(
    rule: ItemFeaturePruningRule,
    *,
    movie_count: int,
) -> tuple[str, dict[str, int | float]]:
    params: dict[str, int | float] = {
        "min_movie_frequency": rule.min_movie_frequency,
        "movie_count": movie_count,
    }
    clauses = ["movie_frequency >= :min_movie_frequency"]
    if rule.max_catalog_ratio is not None:
        clauses.append(
            "movie_frequency::double precision / :movie_count <= :max_catalog_ratio"
        )
        params["max_catalog_ratio"] = rule.max_catalog_ratio
    return " AND ".join(clauses), params


def _high_frequency_drop_clause(rule: ItemFeaturePruningRule) -> str:
    if rule.max_catalog_ratio is None:
        return "false"
    return (
        "movie_frequency >= :min_movie_frequency AND "
        "movie_frequency::double precision / :movie_count > :max_catalog_ratio"
    )


def _hash_ordered_values(label: str, values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{label}\n".encode())
    for value in values:
        digest.update(f"{value}\n".encode())
    return digest.hexdigest()


def _hash_export(
    *,
    build: OntologyBuild,
    movie_mapping_hash: str,
    feature_mapping_hash: str,
    matrix: csr_matrix,
    pruning_rules: Mapping[FeatureName, ItemFeaturePruningRule],
) -> str:
    digest = hashlib.sha256()
    digest.update(f"exporter:{ITEM_FEATURE_EXPORTER_VERSION}\n".encode())
    digest.update(f"build:{build.id}:{build.engine_name}:{build.schema_version}\n".encode())
    digest.update(f"source:{build.source_hash}\n".encode())
    digest.update(f"movies:{movie_mapping_hash}\n".encode())
    digest.update(f"features:{feature_mapping_hash}\n".encode())
    for feature, rule in sorted(pruning_rules.items(), key=lambda item: item[0].value):
        digest.update(
            (
                f"rule:{feature.value}:{rule.min_movie_frequency}:"
                f"{rule.max_catalog_ratio}\n"
            ).encode()
        )
    digest.update(np.asarray(matrix.indptr, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.indices, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.data, dtype="<f4").tobytes())
    return digest.hexdigest()
