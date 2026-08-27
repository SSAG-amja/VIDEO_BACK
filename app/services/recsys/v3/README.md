# V3 Online Service

This package owns recommendation work executed in or directly supporting an API request. It must not import `app.jobs.recsys.v3`.

## Public Entry Points

- `adapter.py`: common recommendation-engine adapter
- `recommender.py`: V3 request orchestration and `RecommendationResponse` assembly
- `config.py`: shared V3 constants
- `errors.py`: V3 serving errors

## Packages

- `domain/`: behavior, feature, ontology, catalog, and profile data contracts
- `profiles/`: runtime user-profile construction
- `retrieval/`: long-term and short-term retrieval, merge, eligibility, ontology analysis, and retrieval schemas
- `cold_start/`: onboarding and feature-only cold-start retrieval and merge
- `policy/`: hard/soft policy evaluation, policy configuration, registry, and quality adjustment
- `serving/`: runtime model loading and active serving-bundle validation

Cross-package imports should follow the pipeline direction. Shared contracts belong in `domain/`; they must not be moved into a downstream package merely to resolve an import cycle.
