# V3 User Seed

This directory contains deterministic test fixtures for the V3 recommendation baseline. It does not modify movie or ontology data.

## Files

- `01_seed_training_users.sql`: 120 model-training users, onboarding, home actions, playlists, and community activity
- `02_seed_cold_users.sql`: 24 post-training users and six post-model onboarding mutations
- `03_seed_post_model_quality_actions.sql`: fixed-model stable controls and real post-model drift actions for 12 known users
- `04_cleanup_post_model_quality_actions.sql`: remove only the dedicated post-model quality playlists/actions
- `sync_redis.py`: rebuild and validate the production Redis interaction cache from current DB actions
- `refresh_runtime_candidates.py`: invoke the production V3 cold-start refresh after bundle activation
- `99_cleanup.sql`: delete only V3 seed users and their request diagnostic runs

All test accounts use password `V3SeedTest123!` and one of these email forms:

```text
v3seed-train-001@pinlm.test ... v3seed-train-120@pinlm.test
v3seed-cold-001@pinlm.test  ... v3seed-cold-024@pinlm.test
```

## Preconditions

- PostgreSQL and Redis containers are running.
- V3 ontology build `22` has `status=success`.
- Run commands from the repository root.
- Do not run the cold seed before the hybrid model and known-user candidate snapshot are built. The cold users must remain absent from the model mapping.

```bash
docker compose up -d db redis
```

## Stage 1: Training Users

Dry-run all SQL and assertions without retaining data:

```bash
docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_seed=false' \
  < tests/v3_user_seed/01_seed_training_users.sql
```

Commit the 120 users:

```bash
docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_seed=true' \
  < tests/v3_user_seed/01_seed_training_users.sql
```

Hydrate the same cache path used by the interaction API and validate DB/Redis equality:

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_user_seed.sync_redis

docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_user_seed.refresh_runtime_candidates
```

The tool calls `record_interaction_cache`. It does not write arbitrary Redis values for actions. It clears and rebuilds only these seed-user keys:

```text
user:{seed_user_id}:recent_actions
user:{seed_user_id}:movie:blacklist
user:{seed_user_id}:recsys:profile_version
user:{seed_user_id}:v3:short_term_candidates
recsys:v3:short_term:scheduled_users:v2 / processing_users:v2
```

`blacklist` is a hard-filter input. Saved/pinned/watched actions populate the 24-hour pending-positive accumulator; the seed tool explicitly schedules one forced refresh per user with positive state. DB timestamps remain the short-term profile source. Run the short-term worker after hydration to materialize changed users before the online baseline:

```bash
docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.workers.short_term_candidate_worker --once --batch-size 144
```

The one-shot command is for deterministic fixture preparation. In a V3 service deployment, keep the queue consumer running independently:

```bash
docker compose --profile v3 up -d recsys-v3-short-term-worker
```

Do not run this worker for every request. Interaction writes only update the accumulator and schedule; the worker claims users after the threshold and debounce policy is satisfied.

## Model and Bundle Boundary

Build and activate the hybrid model only after stage 1. Record the paths printed by each command.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.training.train_hybrid_model 22

docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.candidates.materialize_candidates \
  assets/ml_models/v3/{model_build_id} --publish

docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.serving.serving_bundle_publisher \
  assets/ml_models/v3/{model_build_id} \
  assets/ml_models/v3/candidate_snapshots/{candidate_snapshot_id}
```

## Stage 2: Cold Users

Run only after the model and candidate snapshot above are fixed.

```bash
docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_seed=true' \
  < tests/v3_user_seed/02_seed_cold_users.sql

docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_user_seed.sync_redis
```

The second seed creates:

- 8 genre + favorite users
- 8 genre-only users
- 4 OTT-only users
- 4 incomplete empty-profile users
- onboarding changes for six known users after their model features were frozen

## Stage 3: Post-Model Known-User Quality Scenario

Run this only after the active model and candidate snapshot are fixed. It does not retrain or replace the published long-term candidates. Six stable controls receive recent movies from their original cohort and six drift users receive recent movies from the configured opposite cohort. The movies require at least 50% target-genre share and 100 votes.

```bash
docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_seed=true' \
  < tests/v3_user_seed/03_seed_post_model_quality_actions.sql

docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_user_seed.sync_redis --quality-post-model

docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.workers.short_term_candidate_worker --once --batch-size 12

docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.diagnostics.quality_snapshot \
  --scenario post-model --limit 20
```

The six saved movies per user live only in a private `v3quality-postmodel-*` playlist. Remove this stage without deleting the users or their historical actions:

```bash
docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_cleanup=true' \
  < tests/v3_user_seed/04_cleanup_post_model_quality_actions.sql
```

## Online Baseline

After stage 2 and runtime candidate refresh, run the V3 adapter invariant and latency baseline:

```bash
docker compose run --rm --no-deps back-seeder \
  python -m app.jobs.recsys.v3.diagnostics.online_baseline
```

The report is written to `z_v3_docs/diagnostics/v3_online_baseline_*.json`. It measures
process-cold and warm requests separately and does not calculate NDCG or Recall.

## Repeatability Boundary

The training SQL resets only existing `v3seed-train-*` users and preserves their user IDs. It refuses to run while cold users exist, because those users must not leak into a rebuilt model mapping. Run Redis cleanup and `99_cleanup.sql` before rebuilding stage 1. Re-running stage 1 changes the behavior snapshot and therefore invalidates any model, candidate snapshot, and serving bundle built from the previous snapshot. Rebuild every downstream artifact after a training-seed rerun.

The cold SQL may be rerun without retraining only when its 24 users were never part of the model mapping. It deliberately mutates onboarding for six known users to exercise feature drift.

## Cleanup

Keep the fixtures while recommendation refinement is ongoing. At final cleanup, remove Redis keys before deleting users because Redis has no foreign-key cascade.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_user_seed.sync_redis --cleanup

docker exec -i PINLM_DB sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -v commit_cleanup=true' \
  < tests/v3_user_seed/99_cleanup.sql
```

`99_cleanup.sql` does not delete movies, ontology build `22`, model files, snapshots, or `active_bundle.json`. Remove or replace the active V3 bundle before deleting users so an online process cannot keep serving a model whose mapped users were removed.
