# V3 Recommendation Handoff

## Current Objective

Implement recommendation V3 as a new engine without removing V1 or V2.

- LightFM: learned long-term candidate retrieval and collaborative signal
- Ontology: LightFM features, semantic evidence, short-term/cold-item retrieval
- Policy engine: filtering, OTT, negative preference, quality, repetition, and final reranking
- Candidate pool: maximum 100 before detailed analysis and policy reranking
- Random/new-release/long-tail exploration: deferred until the accuracy baseline is stable
- Deterministic repetition penalties and MMR: included in the first V3 scope

Read these documents before implementation:

1. `z_v3_docs/v3_design_and_implementation_plan.md`
2. `z_v3_docs/v3_ontology_redesign.md`

The V3 adapter/service/job scaffold exists, but the model, ontology analyzer, policy engine, and serving pipeline are not implemented. Start with Phase 0 and the implementation gates in the design document.

## Reference Rules

V1 is the default recommendation-policy baseline, but it is not mandatory in every case.

- Use V1 first for behavior meaning, profile construction, exclusion, OTT, cold-start, dynamic fill, and worker safety.
- A V2 policy may be selected when V1 has no equivalent, or when the same evaluation shows that V2 is better.
- Record each decision as `v1`, `v2`, or `v3_new` with metrics and reasoning. Do not adopt V2 only because it is newer.
- Use V2 primarily for ontology graph/build/evidence assets and set-based graph query patterns.
- Do not import the V2 scorer/ranker pipeline as the V3 policy engine.

Primary V1 references:

- `app/jobs/recsys/v1/worker.py`
- `app/services/recsys/v1/recommendation.py`
- `app/services/recsys/v1/dynamic_retriever.py`
- `app/services/recsys/v1/interaction_cache.py`

Primary V2 ontology references:

- `app/services/recsys/v2/graph_builder.py`
- `app/jobs/recsys/v2/overview_signal_extractor.py`
- `app/jobs/recsys/v2/materialize_overview_edges.py`
- `app/jobs/recsys/v2/validate_assets.py`
- `app/models/ontology.py`
- `assets/ontology/`

## Planned Locations

- Existing HTTP API routes and schemas: `app/api/v1/` (preserve paths and response contracts)
- Recommendation engine contracts, registry, and adapters: `app/services/recsys/`
- Online V3 service: `app/services/recsys/v3/`
- Training/materialization jobs: `app/jobs/recsys/v3/`
- V3 ML dependency pins: `requirements-recsys-v3.txt`
- Recommendation CRUD: `app/crud/recsys/`
- SQLAlchemy models: `app/models/`
- Alembic migrations: `app/db/alembic/versions/`
- Model artifacts: `assets/ml_models/lightfm/` (do not commit binary artifacts)

## Non-Negotiable Boundaries

- Preserve existing user changes in the dirty worktree. Do not revert unrelated files.
- Treat API V1 and recommendation-engine V1 as separate version axes. Do not add `app/api/recsys/v3` or V3-specific HTTP routes.
- API endpoints may delegate to the common service registry, but engine-specific imports and branching belong outside the API layer.
- Keep V1 and V2 runnable behind the existing engine switch.
- Do not delete or overwrite active V2 ontology data while building V3.
- V2/V3 ontology activation must be scoped by schema version; the current global active-build update cannot be reused unchanged.
- Activate model, ontology, and policy versions as one validated serving bundle.
- Never create a dense user-by-all-movies score matrix. Use blockwise exact top-K first.
- Never scan the full ontology graph or issue candidate-by-candidate graph queries online. Use indexed, set-based, bounded queries.
- Generate short-term ontology candidates independently when recent positive signals exist; reranking only the LightFM pool is insufficient.
- Keep LightFM score, ontology score/evidence, policy effects, final score trace, and user-facing reasons separate.
- Ontology evidence is semantic support, not causal attribution for the LightFM score.
- `RECOMMENDATION_ENGINE=v3` currently selects the V3 adapter, which deliberately raises `V3NotReadyError` until the serving pipeline exists. Do not silently route it to V1.
- LightFM 1.17 is built in a `python:3.11` Docker stage; runtime remains `python:3.11-slim` with `libgomp1`.
- The dependency spike is `python -m app.jobs.recsys.v3.lightfm_dependency_spike`; it must keep covering feature-only user/item inference and artifact reload.
