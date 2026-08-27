# ML Models

This directory stores local recommendation model artifacts.

V3 identity-only and ontology-hybrid artifacts are published under:

```text
assets/ml_models/v3/{model_build_id}/
```

Hybrid artifacts also contain sparse user/item features, feature token mappings, ontology/export manifests, and the feature registry version. The publisher validates file hashes, mappings, dimensions, and prediction equality after reload before atomically exposing a build. Model binaries remain ignored by Git and must not be committed.

Validated V3 LightFM candidate snapshots are published under:

```text
assets/ml_models/v3/candidate_snapshots/{candidate_snapshot_id}/
```

Incomplete runs remain in a hidden `.inprogress` directory so completed checkpoint shards can be reused. A snapshot records model raw score and source rank only; ontology, policy, final rank, and explanation data are added by later stages.

Validated serving bundles are immutable manifests under:

```text
assets/ml_models/v3/serving_bundles/{bundle_id}/manifest.json
assets/ml_models/v3/active_bundle.json
```

`active_bundle.json` is the only online activation pointer. Activate only after publishing the candidate snapshot to DB:

```bash
python -m app.jobs.recsys.v3.candidates.materialize_candidates \
  assets/ml_models/v3/{model_build_id} --publish

python -m app.jobs.recsys.v3.serving.serving_bundle_publisher \
  assets/ml_models/v3/{model_build_id} \
  assets/ml_models/v3/candidate_snapshots/{candidate_snapshot_id}
```

The API process validates and caches the active model once. Invalid pointer changes keep the previous in-memory bundle; a process with no valid bundle raises `V3NotReadyError`.
