from __future__ import annotations

import unittest
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.recsys.v3.ontology.ontology_build_pipeline import (
    build_asset_manifest,
    compute_manifest_hash,
)
from app.jobs.recsys.v3.ontology.ontology_asset_validator import validate_assets
from app.jobs.recsys.v3.ontology.ontology_graph_builder import (
    CatalogChunk,
    INCOMPLETE_BUILD_TABLES,
    LEGACY_RELATION_NAMES,
    V3OntologyGraphBuilder,
    aggregate_evidence_strength,
)
from app.models.ontology import OntologyBuild, OntologyEdge, OntologyEdgeEvidence
from app.services.recsys.v3.domain.ontology_registry import (
    NodeType,
    RelationConsumer,
    get_relation_definition,
    relations_for_consumer,
    validate_edge_contract,
    validate_relation_registry,
)


class OntologySchemaTest(unittest.TestCase):
    def test_graph_builder_defaults_to_four_workers(self) -> None:
        builder = V3OntologyGraphBuilder(db=None)  # type: ignore[arg-type]

        self.assertEqual(builder.worker_count, 4)
        with self.assertRaises(ValueError):
            V3OntologyGraphBuilder(db=None, worker_count=0)  # type: ignore[arg-type]

    def test_failed_build_reset_orders_fk_children_before_parents(self) -> None:
        self.assertEqual(
            INCOMPLETE_BUILD_TABLES,
            (
                ("ontology_edge_evidence", "reset_edge_evidence"),
                ("ontology_edges", "reset_edges"),
                ("ontology_nodes", "reset_nodes"),
            ),
        )

    def test_foreign_key_child_columns_have_leading_indexes(self) -> None:
        def leading_index_columns(model: type[object]) -> set[str]:
            return {
                next(iter(index.columns)).name
                for index in model.__table__.indexes  # type: ignore[attr-defined]
                if len(index.columns) > 0
            }

        edge_indexes = leading_index_columns(OntologyEdge)
        evidence_indexes = leading_index_columns(OntologyEdgeEvidence)

        self.assertIn("source_node_id", edge_indexes)
        self.assertIn("target_node_id", edge_indexes)
        self.assertIn("edge_id", evidence_indexes)

    def test_semantic_evidence_stage_is_shared_across_stage_commits(self) -> None:
        class RecordingSession:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, statement: object, *_args: object, **_kwargs: object) -> object:
                self.statements.append(str(statement))
                return SimpleNamespace(rowcount=0)

        db = RecordingSession()
        builder = V3OntologyGraphBuilder(db=db)  # type: ignore[arg-type]
        build = OntologyBuild(
            id=42,
            engine_name="v3",
            schema_version="v3.0",
            version="v3.0.0",
            status="running",
            is_active=False,
            source_hash="stage-name-test",
        )
        with (
            patch.object(builder, "_load_assets", return_value={}),
            patch.object(builder, "_materialize_graph", return_value=(0, 0, 0)),
        ):
            builder.build(build)

        builder._create_semantic_evidence(build.id)
        statements = "\n".join(db.statements)

        self.assertEqual(
            builder._evidence_stage_name,
            "public.v3_ontology_evidence_42",
        )
        self.assertIn(
            "CREATE UNLOGGED TABLE public.v3_ontology_evidence_42",
            statements,
        )
        self.assertNotIn("CREATE TEMP TABLE", statements)

    def test_dynamic_chunk_queue_processes_every_chunk_once(self) -> None:
        class FakeSession:
            def __enter__(self) -> "FakeSession":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, *_args: object, **_kwargs: object) -> None:
                return None

            def commit(self) -> None:
                return None

            def rollback(self) -> None:
                return None

        seen: list[int] = []
        seen_lock = Lock()
        builder = V3OntologyGraphBuilder(
            db=FakeSession(),  # type: ignore[arg-type]
            batch_size=2,
            worker_count=4,
            session_factory=FakeSession,  # type: ignore[arg-type]
        )
        builder._catalog_chunks = tuple(
            CatalogChunk(
                start_ordinal=start,
                end_ordinal=start + 2,
                start_movie_id=start * 10,
                end_movie_id=start * 10 + 1,
            )
            for start in range(1, 11, 2)
        )

        def process_chunk(_db: object, chunk: CatalogChunk) -> int:
            with seen_lock:
                seen.append(chunk.start_ordinal)
            return chunk.end_ordinal - chunk.start_ordinal

        rows, metric = builder._run_catalog_chunk_queue("test", process_chunk)  # type: ignore[arg-type]

        self.assertEqual(rows, 10)
        self.assertEqual(sorted(seen), [1, 3, 5, 7, 9])
        self.assertEqual(metric["worker_count"], 4)
        self.assertEqual(metric["chunk_count"], 5)
        self.assertEqual(
            sum(worker["chunks"] for worker in metric["worker_metrics"]),
            5,
        )

    def test_build_identity_is_scoped_by_engine_schema_and_source(self) -> None:
        unique_constraints = {
            constraint.name
            for constraint in OntologyBuild.__table__.constraints
            if constraint.name is not None
        }

        self.assertIn(
            "uq_ontology_builds_engine_schema_source_hash",
            unique_constraints,
        )
        self.assertIn("build_id", OntologyEdgeEvidence.__table__.columns)
        self.assertIn("edge_id", OntologyEdgeEvidence.__table__.columns)

    def test_actor_and_director_target_person_nodes(self) -> None:
        actor = get_relation_definition("has_actor")
        director = get_relation_definition("has_director")

        self.assertEqual(actor.target_type, NodeType.PERSON)
        self.assertEqual(director.target_type, NodeType.PERSON)

    def test_semantic_derivation_uses_same_relation_for_genre_and_keyword(self) -> None:
        genre = get_relation_definition(
            "suggests_theme",
            source_type=NodeType.GENRE,
            target_type=NodeType.THEME,
        )
        keyword = get_relation_definition(
            "suggests_theme",
            source_type=NodeType.KEYWORD,
            target_type=NodeType.THEME,
        )

        self.assertEqual(genre.relation_type, keyword.relation_type)
        with self.assertRaises(ValueError):
            get_relation_definition("suggests_theme")

    def test_canonical_semantic_edges_require_evidence(self) -> None:
        with self.assertRaises(ValueError):
            validate_edge_contract(
                relation_type="has_theme",
                source_type="movie",
                target_type="theme",
                weight=1.0,
                confidence=1.0,
                effective_strength=0.8,
                evidence_count=0,
            )

        validate_edge_contract(
            relation_type="has_theme",
            source_type="movie",
            target_type="theme",
            weight=1.0,
            confidence=1.0,
            effective_strength=0.8,
            evidence_count=2,
        )

    def test_ott_relations_are_not_lightfm_feature_relations(self) -> None:
        validate_relation_registry()
        exported_relations = {
            definition.relation_type
            for definition in relations_for_consumer(RelationConsumer.FEATURE_EXPORTER)
        }

        self.assertFalse(
            {
                "available_streaming_on",
                "available_rent_on",
                "available_buy_on",
            }
            & exported_relations
        )

    def test_semantic_aggregation_caps_each_source_family(self) -> None:
        strength = aggregate_evidence_strength(
            [
                ("genre_rule", 0.4),
                ("genre_rule", 0.7),
                ("keyword_rule", 0.5),
            ]
        )

        self.assertAlmostEqual(strength, 0.85)

    def test_semantic_aggregation_rejects_out_of_range_evidence(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_evidence_strength([("overview_signal", 1.01)])

    def test_graph_builder_rejects_v2_and_active_builds(self) -> None:
        builder = V3OntologyGraphBuilder(db=None)  # type: ignore[arg-type]
        v2_build = OntologyBuild(
            id=1,
            engine_name="v2",
            schema_version="v2",
            version="v2.0.0",
            status="running",
            is_active=False,
            source_hash="v2",
        )
        active_v3_build = OntologyBuild(
            id=2,
            engine_name="v3",
            schema_version="v3.0",
            version="v3.0.0",
            status="success",
            is_active=True,
            source_hash="v3",
        )

        with self.assertRaises(ValueError):
            builder._validate_build(v2_build)
        with self.assertRaises(ValueError):
            builder._validate_build(active_v3_build)

    def test_all_source_asset_relations_map_to_v3_registry(self) -> None:
        builder = V3OntologyGraphBuilder(db=None)  # type: ignore[arg-type]
        assets = builder._load_assets()

        for payload in assets.values():
            for relation in payload.get("relations", []):
                relation_type = LEGACY_RELATION_NAMES.get(
                    relation["relation_type"], relation["relation_type"]
                )
                get_relation_definition(
                    relation_type,
                    source_type=relation["source_type"],
                    target_type=relation["target_type"],
                )

    def test_source_manifest_hash_is_stable_and_tracks_assets(self) -> None:
        assets = build_asset_manifest()
        manifest = {"schema": "v3.0", "assets": assets}

        self.assertEqual(compute_manifest_hash(manifest), compute_manifest_hash(manifest))
        self.assertIn("genre_theme_mood_rules.json", assets)
        changed_manifest = {
            **manifest,
            "assets": {
                **assets,
                "genre_theme_mood_rules.json": {
                    **assets["genre_theme_mood_rules.json"],
                    "version": "changed",
                },
            },
        }
        self.assertNotEqual(
            compute_manifest_hash(manifest),
            compute_manifest_hash(changed_manifest),
        )

    def test_enriched_assets_cover_every_controlled_concept(self) -> None:
        report = validate_assets()

        self.assertEqual(report.asset_version, "0.2.0")
        self.assertEqual(report.theme_count, 30)
        self.assertEqual(report.derived_theme_count, report.theme_count)
        self.assertEqual(report.mood_count, 16)
        self.assertEqual(report.derived_mood_count, report.mood_count)
        self.assertLessEqual(report.relation_count, 120)


if __name__ == "__main__":
    unittest.main()
