# Recommendation Handoff

## Current V4 Objective

Build V4 as an independent engine while preserving V1, V2, and V3. V4 removes
the parallel long-term ontology candidate lane: ontology supplies semantic user
and item features to Hybrid LightFM, and Hybrid LightFM exclusively owns
long-term candidate generation. Direct ontology retrieval remains limited to
short-term preference changes and cold-start; bounded ontology analysis remains
available for ranking evidence and explanations.

Read `z_v4_docs/01_v4_architecture_and_implementation_plan.md`,
`app/services/recsys/v4/README.md`, and `app/jobs/recsys/v4/README.md` before V4
changes. V4 owns `app/services/recsys/v4`, `app/jobs/recsys/v4`,
`assets/ontology/v4`, `assets/ml_models/v4`, `lightfm_v4` candidate rows, and
Redis keys in the `v4` namespace. V4 packages must not import V3 packages.

V4 ontology build `24` completed and passed validation in 398 seconds. No V4
model, candidate snapshot, or serving bundle has been activated yet. Selecting
V4 must raise `V4NotReadyError` until those artifacts are validated. Do not use
the active V3 bundle as a V4 compatibility fallback.

The initial V4 holdout run trained nested 100/200/300/400/500-user shadow
models and evaluated the same anchor 100 users. NDCG@100% was
`0.875735/0.878703/0.878545/0.877341/0.877953`; the matching pure logistic
LightFM baseline was `0.877307/0.879962/0.880619/0.883570/0.883873`. These are
ranking-only diagnostics over each user's temporal 30% holdout and do not prove
catalog retrieval quality. V4 remains below the pure baseline at every scale;
compare selectable LightFM losses under equal conditions before activation.

The engine-scoped recommendation migration `e7f9a1b3c5d7` is applied in the
local database. V4 architecture/lifecycle tests pass, all V4 modules import, and
the V3 regression suite remains green. Do not claim V4 is operational until the
full V4 ontology build, model training, candidate publication, and bundle
activation are completed.

## V3 Baseline Reference

## Current Objective

Maintain and improve recommendation V3 as an independent engine without removing V1 or V2. The S0-S9 baseline and quality Phases A-I are complete; select new work from `z_v3_docs/08_additional_work_backlog.md`.

- LightFM: learned long-term candidate retrieval and collaborative signal
- Ontology: LightFM features, semantic evidence, short-term/cold-item retrieval
- Policy engine: filtering, OTT, negative preference, quality, repetition, and final reranking
- Candidate storage: top-150 per source merge, consisting of 100 active ranks and 50 ordered hard-filter reserves
- Detailed analysis and policy reranking: maximum 100 eligible candidates
- Random/new-release/long-tail exploration: deferred until the accuracy baseline is stable
- Deterministic repetition penalties and MMR: included in the first V3 scope

Read these documents before implementation:

1. `z_v3_docs/README.md`
2. `z_v3_docs/01_architecture_and_pipeline.md`
3. `z_v3_docs/07_end_to_end_flow_review.md` before changing recommendation flow or policy
4. `z_v3_docs/08_additional_work_backlog.md` for post-baseline priorities and pending work
5. `z_v3_docs/09_design_decision_journal.md` before revisiting an existing architectural decision
6. `z_v3_docs/10_quality_improvement_record.md` before quality tuning
7. Select the task-specific source of truth from `02_implementation_guide.md`, `03_recommendation_policy.md`, `04_lightfm_tuning.md`, `05_ontology_structure.md`, and `06_validation_record.md`.
8. `v3_recommendation_quality_tuning_plan.md` for the current long-term ranking work and `v3_recommendation_quality_tuning_results.md` for retained evidence.

Current correction: candidate materialization stores top-150 per user as 100 active plus 50 ordered reserves, while detailed ontology analysis and policy reranking remain capped at 100. Feed-session continuity remains deferred in document 08. Phase I corrected synthetic-seed overlap, capped popular-item and high-activity-user training influence, reduced user identity dominance, and added population/user collaborative confidence. Confidence scales only the LightFM user-identity contribution; it must not reduce the whole LightFM candidate lane or transfer its weight to ontology. For known users, model/ontology candidate selection starts at 65/35 when top-50 movie overlap is zero and moves at most to 55/45 as overlap rises. Candidate selection is not reused as the final personal score; final personal uses model/short-term scores and detailed ontology is added once. The active model is `hybrid-6e5ae61ef3bb-8ee76ae0cc79-51c5cf5ab1c1-32da0f11b507-6f0cb1a5dc05-7b869d3b`, candidate snapshot is `cand-91eaed36f64d20baacfd7360`, and bundle is `bundle-1c3b3416d156bfdfc2a1a0a4`.

Long-term preference ranking now uses holdout-wide NDCG@100% as its primary metric and NDCG@20% only as a secondary diagnostic. TUNE-21 and TUNE-22 isolated behavior input, identity representation, and training configuration on two disjoint 200-user development cohorts. TUNE-23 then excluded all 800 previously consumed users and compared only the pure baseline with the locked V3 core on 200 untouched users. Locked V3 core scored `0.879191` versus `0.876445`, a `+0.002747` paired gain with bootstrap 95% CI `[+0.000272, +0.005317]`; all three seeds and both stable-focused/stable-diverse segments were positive. Keep V3 behavior input, supported identity, and `D16/E60/LR0.025/alpha 1e-5` fixed for the next quality stage. The gain is small and applies only to ranking the supplied temporal holdout, not catalog retrieval quality.

TUNE-24 tested the separate final ontology score on development cohorts A/B with the locked core, build 22 graph edges, runtime long-term profile rules, and field-budget item features. Current `75% model + 25% ontology` changed NDCG@100% by `-0.001513` on A and `-0.000844` on B; all seed deltas were negative and stable-diverse users lost `-0.002844`. Positive graph coverage was 100%, candidate ontology match rate was above 99%, and mean model/ontology rank correlation was near zero. Treat unconditional 25% ontology fusion for established long-term users as unvalidated. Next isolate overbroad profile/family scoring from percentile amplification; do not remove ontology retrieval, cold-start, or sparse-user use, and do not activate a new bundle yet.

TUNE-25 isolated profile breadth from score normalization. Requiring two supporting positive movies removed only about one of 100 retained features per dense user and had no useful ranking effect. Replacing candidate-relative percentile normalization with the already field-budget-bounded raw ontology score improved NDCG@100% by `+0.001407` over current fusion, with a positive bootstrap interval and positive directions on both development cohorts, all seeds, and both profile types. Current-profile bounded fusion was only `+0.000229` over model-only and still lost on seed 44 and stable-diverse users, so it is a diagnostic baseline rather than an accepted production setting. Keep the current profile, use bounded raw for the next family-level diagnostic, and leave production policy and the active bundle unchanged.

TUNE-26 removed one bounded-raw ontology family at a time without redistributing its budget. Director was the only consistently useful family: removing it changed NDCG@100% by `-0.000705`, with a fully negative bootstrap interval and negative directions on both cohorts, all seeds, and both profile types. Mood removal improved the mean by `+0.000263`, but its interval included zero and stable-focused users were neutral; genre and theme also had opposite effects by profile type. Do not globally delete any family. Full bounded fusion still helped stable-focused users by `+0.001870` and hurt stable-diverse users by `-0.001412`; no single-family removal fixed that split. Next diagnose whether a serving-available long-term preference-concentration measure supports conditional ontology fusion. Production policy and the active bundle remain unchanged.

TUNE-27 related the bounded ontology effect to normalized genre entropy computed only from positive movies in each user's 70% training window. Entropy/effect correlation was weak but directionally replicated: Pearson `-0.1062`, Spearman `-0.1439`, and every cohort and seed correlation was negative. Mean effects decreased monotonically from `+0.002128/+0.001611` in the two low-entropy quartiles to `-0.001090/-0.001733` in the high-entropy quartiles. Positive behavior count had no relationship (`+0.0054`). This supports further investigation, not a serving rule: the tail-selected cohorts have no users between entropy `0.802862` and `0.864500`, and individual outcomes overlap heavily. Do not select a threshold or change production yet. TUNE-28 must use unused users spanning low, middle, and high entropy before testing conditional fusion.

TUNE-28 used 600 new users with no overlap against the prior 1,000 evaluation users. Each of two cohorts has 100 low-, 100 middle-, and 100 high-entropy stable users, matched in triplets by dominant genre and activity. Bounded raw `75/25` beat V3 core model-only by `+0.001393`, CI `[+0.000301,+0.002468]`; both cohorts, all seeds, and all three entropy bins were positive. Entropy/effect Pearson and Spearman correlations fell to `-0.0574/-0.0294`, and all four entropy quartiles gained, so TUNE-27's high-entropy loss did not generalize. Reject entropy-based conditional fusion. Keep current-profile bounded raw `75/25` as the fixed candidate, but do not activate it. TUNE-29 should exclude TUNE-28 users and compare only pure LightFM, locked V3 core, and locked V3 core plus bounded ontology on a fresh continuous-distribution cohort.

The structural feature corrections remain valid: preserve onboarding-only L1, keep long-term item-field-budget weighted averages without upward normalization, and multiply the long-term block by `positive_movie_count / (positive_movie_count + 5)`. Sparse-item field budgets, keyword IDF, catalog trust, split filters, ontology score de-duplication, and separated score traces are not NDCG tuning candidates unless a regression is demonstrated.

New shadow training defaults `TRAINING_ITEM_FREQUENCY_WEIGHTING` to `none`: LightFM should learn personal relevance without reducing a positive solely because the movie is popular. The old active checkpoint still contains `inverse_sqrt`. Candidate-level popular-item pressure is implemented as an opt-in bounded policy rerank setting and defaults to zero until R6 selects a value; do not activate the no-frequency model before concentration and final rerank validation.

Parallel processing and dynamic scheduling (`z_v3_docs/08_additional_work_backlog.md` P3-00) are complete. API recommendation calls use a shared 2-thread bounded executor and a fresh SQLAlchemy session per task; `/shorts` no longer runs V3 synchronously on the event loop. Candidate materialization supports a shared dynamic user-block queue but keeps 1 worker as the default because 2/4 workers did not improve the vectorized batch. The 1/2/4-worker result hashes were identical; retain `RECOMMENDATION_EXECUTOR_WORKERS=2` and candidate worker 1 unless a new workload benchmark supports changing them.

The user reopened implementation for package organization. V3 services and jobs are now grouped by responsibility; preserve the package tree documented in `app/services/recsys/v3/README.md`, `app/jobs/recsys/v3/README.md`, and `z_v3_docs/02_implementation_guide.md` instead of adding new root-level modules.

The V3 S0-S9 components exist: engine boundary, dataset/feature/graph contracts, hybrid LightFM, top-150 candidate storage, runtime profile, independent short-term retrieval, policy/cold-start, and serving bundle orchestration. Short-term candidates use the documented 24-hour accumulator, threshold, debounce, lease, and cache-format-3 behavior. Full graph build `22` and the full item export remain the validated ontology baseline. Component and single-request tests pass, but feed-session continuity and production orchestration are open skeleton work. Keep the five consumed-user evaluation plans and active artifacts until this validation iteration ends. Holdout ranking NDCG is in scope and its LightFM core gate has passed; Recall and full-catalog candidate retrieval remain outside the completed validation.

Quality Phases A-I are complete. Phase I's unadjusted model reduced representative LightFM top-20 pairwise Jaccard from 21.2% to 9.8% while preserving stable genre overlap at 85.8%. The 120-user synthetic model has population confidence `0.1556`; this attenuates only the learned user-identity component during LightFM scoring. Corrected integrated validation produced 310 unique movies in 480 final slots and 90 unique movies in 120 top-5 slots. Target-genre top-5 matches were stable 30/30, drift-current 30/30, negative-heavy 30/30, and mixed 29/30, with zero exclusion or within-user duplicate violations. Base and effective model weights stayed equal. Long-term catalog qualification is now split from request-time filtering: static catalog and mature cold-item trust run before LightFM/ontology Top-K, while watched/passed, session, blacklist, and OTT remain request conditions. Four long-term user types retained 100 final candidates with no held-out positive rejected; short-term catalog-trust validation remains. Reports include `tests/v3_long_term_eval/outputs/h2_cross_validation/*_split_filter.json`. Actual-user relevance and population calibration remain unproven. Operations, traffic optimization, and auxiliary policy work are deferred while recommendation quality is the user priority. The latest V3 unit-test count is 145, plus 2 shared recommendation-executor tests.

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

## Locations

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
