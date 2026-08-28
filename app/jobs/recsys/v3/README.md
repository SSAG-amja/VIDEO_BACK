# V3 Offline Jobs

This package owns work that runs outside an API request: graph builds, dataset and feature export, model training, artifact publication, and candidate materialization.

## Entry Points

- `ontology/ontology_build_pipeline.py`: immutable V3 graph build orchestration
- `training/train_identity_model.py`: identity-only baseline build
- `training/train_hybrid_model.py`: ontology-hybrid model build
- `candidates/materialize_candidates.py`: exact top-150 snapshot build (100 active + 50 reserve) and optional DB publication
- `serving/serving_bundle_publisher.py`: compatible model/graph/candidate/policy validation and atomic activation
- `workers/worker.py`: scheduler-facing candidate materialization function
- `workers/short_term_candidate_worker.py`: threshold/debounce scheduled-user consumer and short-term candidate cache refresh

## Internal Components

- `datasets/`: snapshot dataset contracts, direct behavior aggregation, and diagnostic social projection
- `features/`: item and user sparse feature export
- `training/`: LightFM trainers, model schemas, executable training jobs, and immutable model artifacts
- `ontology/`: ontology graph build, orchestration, and asset validation
- `candidates/`: blockwise materialization, snapshot schema/storage, and DB publication
- `serving/`: serving-bundle compatibility validation and activation
- `workers/`: scheduler-facing and short-term background workers

## Diagnostics

`diagnostics/` contains manually invoked development tools. They are not production scheduler entry points and production modules must not import them.

- `lightfm_dependency_spike.py`: dependency, feature-only inference, and reload gate
- `parallel_build_benchmark.py`: destructive temporary-table benchmark for graph worker count
- `online_baseline.py`: seeded V3 online invariant and per-user latency baseline
- `online_stage_profile.py`: representative online stage latency breakdown
- `short_term_refresh_policy_check.py`: Redis-backed threshold, debounce, removal, and queue-state diagnostic
- `item_feature_export_diagnostics.py`: full item CSR time, memory, and coverage report
- `quality_snapshot.py`: seeded known-user long-term, short-term, and final recommendation quality snapshot
- `ontology_component_ablation.py`: fixed-candidate policy comparison for personal/ontology `1.00/0.00` versus `0.75/0.25`
- `catalog_negative_ablation.py`: fixed-candidate comparison for low-vote catalog trust and semantic-negative policy effects
- `ontology_outlier_audit.py`: quality-snapshot outlier extraction with exact graph feature matches for diagnosis
- `candidate_parallel_benchmark.py`: 1/2/4-worker candidate materialization throughput, memory, and result-invariant comparison
- `request_parallel_benchmark.py`: 1/2/4-worker warm serving throughput, latency, memory, and result-invariant comparison

## Boundaries

- HTTP and online recommendation code belongs in `app/services/recsys/v3` and must not import this package.
- Shared behavior contracts, catalog eligibility, policy configuration, and runtime profile logic belong in `app/services/recsys/v3`.
- Static ontology definitions belong in `assets/ontology/v3`.
- Generated model and candidate artifacts belong under the ignored `assets/ml_models/v3` path.
- A new executable job may orchestrate internal components, but reusable online logic must not be added here.
