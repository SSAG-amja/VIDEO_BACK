# Recommendation V4 Service

V4 is an independently selectable recommendation engine. It owns its runtime
contracts, policy, profile, retrieval, cold-start, short-term cache, and serving
bundle loader. V4 service and job packages must not import V3 packages.

```text
known user: V4 Hybrid LightFM -> optional V4 short-term ontology blend
            -> eligibility -> bounded semantic analysis -> policy reranking

cold user:  V4 feature-only LightFM + cold-start ontology/rules
            -> eligibility -> bounded semantic analysis -> policy reranking
```

Ontology never creates a parallel long-term candidate lane. It supplies semantic
features to Hybrid LightFM and bounded evidence to short-term, cold-start, and
reranking stages.

## Package ownership

- `domain/`: V4 feature, behavior, profile, and ontology contracts
- `profiles/`: V4 runtime user profile construction
- `retrieval/`: V4 LightFM, short-term, eligibility, and semantic analysis
- `cold_start/`: V4 cold-start candidate handling
- `policy/`: V4 filtering and final reranking
- `serving/`: V4 model and serving-bundle loading
- `recommender.py`: V4 request orchestration
- `profile_change.py`: V4 Redis short-term refresh namespace

V4 reads only `assets/ml_models/v4` bundles and `lightfm_v4` candidate rows.
Until a V4 graph, model, candidate snapshot, and serving bundle are generated and
activated, selecting `RECOMMENDATION_ENGINE=v4` raises `V4NotReadyError`.

See `app/jobs/recsys/v4/README.md` for the offline lifecycle and
`z_v4_docs/01_v4_architecture_and_implementation_plan.md` for the architecture.
