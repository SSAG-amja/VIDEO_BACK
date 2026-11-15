# Recommendation V4 Jobs

V4 owns the complete offline lifecycle and writes only V4-scoped artifacts.

```text
datasets/   behavior snapshot and interaction confidence
ontology/   V4 ontology schema build and asset validation
features/   item and long-term user semantic feature matrices
training/   identity baseline and Hybrid LightFM training/publication
candidates/ blockwise top-150 materialization and lightfm_v4 publication
serving/    atomic V4 serving-bundle activation
workers/    candidate materialization and V4 short-term refresh workers
```

## Build order

```bash
python -m app.jobs.recsys.v4.ontology.ontology_build_pipeline
python -m app.jobs.recsys.v4.training.train_hybrid_model <ontology_build_id> --loss warp
python -m app.jobs.recsys.v4.candidates.materialize_candidates <model_artifact> --publish
python -m app.jobs.recsys.v4.serving.serving_bundle_publisher <model_artifact> <candidate_snapshot>
```

Supported experimental LightFM losses are `logistic`, `bpr`, `warp`, and
`warp-kos`. The default remains `warp` until equal-condition evaluation selects a
different loss. Model artifacts, candidate snapshots, and the active pointer live
under `assets/ml_models/v4` and must not overwrite V3 artifacts.

V4 declares its ML dependencies in `requirements-recsys-v4.txt`. The runtime
image builds both V3 and V4 requirement sets in the common Python 3.11 wheel
stage, while each engine keeps an explicit dependency contract.

V4 ontology assets live under `assets/ontology/v4`. The initial assets preserve
the validated V3 semantic vocabulary, but builds are stored with engine `v4` and
schema `v4.0`, so activation cannot change the active V3 ontology build.

The ontology build command performs a full catalog build. Use `--help` only to
inspect the command; it exits without creating a build.
