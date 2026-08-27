# V3 Recommendation Handoff

## Current Objective

Implement recommendation V3 as a new engine without removing V1 or V2.

- LightFM: learned long-term candidate retrieval and collaborative signal
- Ontology: LightFM features, semantic evidence, short-term/cold-item retrieval
- Policy engine: filtering, OTT, negative preference, quality, repetition, and final reranking
- Candidate storage: top-150 per source merge, consisting of 100 active ranks and 50 ordered hard-filter reserves
- Detailed analysis and policy reranking: maximum 100 eligible candidates
- Random/new-release/long-tail exploration: deferred until the accuracy baseline is stable
- Deterministic repetition penalties and MMR: included in the first V3 scope

Read these documents before implementation:

1. `z_v3_docs/README.md`
2. `z_v3_docs/01_design_sequence.md`
3. `z_v3_docs/07_end_to_end_flow_review.md` before changing recommendation flow or policy
4. `z_v3_docs/08_additional_work_backlog.md` for post-baseline priorities and pending work
5. `z_v3_docs/09_design_decision_journal.md` before revisiting an existing architectural decision
6. `z_v3_docs/10_v1_v2_skeleton_audit.md` before any new recommendation implementation
7. `z_v3_docs/11_recommendation_quality_baseline.md` and `12_recommendation_quality_improvement_plan.md` before quality tuning
8. Select the task-specific source of truth from `02_implementation_guide.md`, `03_recommendation_policy.md`, `04_lightfm_tuning.md`, `05_ontology_structure.md`, and `06_test_plan.md`.

Current correction: candidate materialization stores top-150 per user as 100 active plus 50 ordered reserves, while detailed ontology analysis and policy reranking remain capped at 100. Feed-session continuity remains deferred and documented; the user accepted the current service baseline and reopened implementation specifically for recommendation quality. Follow documents 11 and 12 instead of reopening service/operation policy. The latest verified V3 unit count is 93, snapshot is `cand-950d86d7f1f978f316f2b773`, and bundle is `bundle-77128ec4c5c9b5404efc3b4b`.

The user reopened implementation for package organization. V3 services and jobs are now grouped by responsibility; preserve the package tree documented in `app/services/recsys/v3/README.md`, `app/jobs/recsys/v3/README.md`, and `z_v3_docs/02_implementation_guide.md` instead of adding new root-level modules.

The V3 S0-S9 components exist: engine boundary, dataset/feature/graph contracts, hybrid LightFM, top-150 candidate storage, runtime profile, independent short-term retrieval, policy/cold-start, and serving bundle orchestration. Short-term candidates use the documented 24-hour accumulator, threshold, debounce, lease, and cache-format-3 behavior. Full graph build `22` and the full item export remain the validated ontology baseline. Component and single-request tests pass, but feed-session continuity and production orchestration are open skeleton work. Keep the fixture and active artifacts until this validation iteration ends; NDCG and Recall remain outside the current validation scope.

Quality Phase A is complete. The post-model scenario keeps the LightFM model and long-term candidates fixed, then adds same-cohort actions to six stable controls and opposite-cohort actions to six drift users. The result is `z_v3_docs/diagnostics/v3_quality_snapshot_20260827T142248Z.*`. Stable/drift confidence is `0.701/0.737`; drift users retain about 14 short-source candidates at eligibility 100 but zero in the final 20. Next work is Phase B LightFM numerical health gates and inactive ablation artifacts. Do not tune final policy weights before stabilizing the model and drift signal.

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
- Candidate materialization and publication: `app/jobs/recsys/v3/candidates/candidate_materializer.py`, `app/jobs/recsys/v3/candidates/candidate_snapshot.py`, `app/jobs/recsys/v3/candidates/candidate_publisher.py`; raw recommendation DB operations remain in `app/crud/recsys/recommendations.py`.
- V3 ML dependency pins: `requirements-recsys-v3.txt`
- V3 policy decisions: `app/services/recsys/v3/policy/policy_registry.py`
- V3 online policy: `app/services/recsys/v3/policy/policy_engine.py`, `app/services/recsys/v3/policy/policy_schemas.py`
- V3 cold-start: `app/services/recsys/v3/cold_start/cold_start_retriever.py`, `app/services/recsys/v3/cold_start/cold_start_merger.py`, `app/services/recsys/v3/cold_start/cold_start_pipeline.py`
- V3 serving runtime: `app/services/recsys/v3/serving/model_store.py`, `app/services/recsys/v3/serving/serving_bundle.py`, `app/services/recsys/v3/retrieval/lightfm_retriever.py`, `app/services/recsys/v3/recommender.py`
- V3 serving activation: `app/jobs/recsys/v3/serving/serving_bundle_publisher.py`
- V3 short-term candidate cache/worker: `app/services/recsys/v3/retrieval/short_term_candidate_cache.py`, `app/jobs/recsys/v3/workers/short_term_candidate_worker.py`
- V3 snapshot dataset contract: `app/jobs/recsys/v3/datasets/dataset_schemas.py`, `app/jobs/recsys/v3/datasets/dataset_builder.py`
- Diagnostic-only social projector: `app/jobs/recsys/v3/datasets/social_signal_projector.py`
- V3 feature registry and profile contracts: `app/services/recsys/v3/domain/feature_registry.py`, `app/services/recsys/v3/domain/schemas.py`
- V3 ontology relation contract: `app/services/recsys/v3/domain/ontology_registry.py`
- V3 ontology graph build: `app/jobs/recsys/v3/ontology/ontology_graph_builder.py`, `app/jobs/recsys/v3/ontology/ontology_build_pipeline.py`
- V3 ontology assets and validation: `assets/ontology/v3/`, `app/jobs/recsys/v3/ontology/ontology_asset_validator.py`
- Recommendation CRUD: `app/crud/recsys/`
- SQLAlchemy models: `app/models/`
- Alembic migrations: `app/db/alembic/versions/`
- Model artifacts: `assets/ml_models/v3/` (do not commit binary artifacts)
- Staged V3 baseline fixtures: `tests/v3_user_seed/` (120 training users, 24 post-model cold users, and optional 12-user post-model quality actions)

## Non-Negotiable Boundaries

- Preserve existing user changes in the dirty worktree. Do not revert unrelated files.
- Treat API V1 and recommendation-engine V1 as separate version axes. Do not add `app/api/recsys/v3` or V3-specific HTTP routes.
- API endpoints may delegate to the common service registry, but engine-specific imports and branching belong outside the API layer.
- Keep V1 and V2 runnable behind the existing engine switch.
- Do not delete or overwrite active V2 ontology data while building V3.
- Full graph build and full-catalog item feature diagnostics passed for build `22`; it is active only through the validated model/ontology/candidate/policy serving bundle.
- V2/V3 ontology activation must be scoped by schema version; the current global active-build update cannot be reused unchanged.
- Activate model, ontology, and policy versions as one validated serving bundle.
- Never create a dense user-by-all-movies score matrix. Use blockwise exact top-K first.
- Never scan the full ontology graph or issue candidate-by-candidate graph queries online. Use indexed, set-based, bounded queries.
- Generate short-term ontology candidates independently when recent positive signals exist; reranking only the LightFM pool is insufficient.
- DB behavior timestamps remain the short-term source of truth. Redis stores pending-positive timing, scheduled/leased work, and bounded candidates; every cache path must retain DB fallback.
- Keep LightFM score, ontology score/evidence, policy effects, final score trace, and user-facing reasons separate.
- Ontology evidence is semantic support, not causal attribution for the LightFM score.
- `RECOMMENDATION_ENGINE=v3` selects the completed V3 serving path. Without a validated active bundle it raises `V3NotReadyError`; do not silently route it to V1 inside the engine.
- LightFM 1.17 is built in a `python:3.11` Docker stage; runtime remains `python:3.11-slim` with `libgomp1`.
- V3 offline package boundaries are documented in `app/jobs/recsys/v3/README.md`. Online `app/services/recsys/v3` code must not import jobs.
- Manually invoked spikes and benchmarks belong in `app/jobs/recsys/v3/diagnostics/` and must not be imported by production jobs.
- The dependency spike is `python -m app.jobs.recsys.v3.diagnostics.lightfm_dependency_spike`; it must keep covering feature-only user/item inference and artifact reload.
- The MVP dataset builder reads current saved/pinned/watched/passed/favorite state. It must not claim immutable event history or infer repeated actions that the current schema does not store.
- Extend collaborative training signals from V1's post/like/reply semantics, but use V3 provenance, caps, event-time playlist projection, and one-row-per-user-movie aggregation. Do not treat current mutable playlist contents as historical truth.
- `likes` currently has no `created_at`; direct movie-post likes may only use a bounded missing-time weight in a current-time snapshot build and must be excluded from historical-cutoff datasets. Playlist-post likes stay deferred until the action-time playlist membership can be reconstructed.
- Passed is never a WARP positive. A passed/positive conflict for one user-movie pair is diagnosed and passed wins.
- Training weights in `app/services/recsys/v3/config.py` are provisional tuning inputs, not inherited V1 score constants.
- OTT subscription and movie availability are serving context and rule inputs, not LightFM user/item features.
