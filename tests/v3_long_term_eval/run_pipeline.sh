#!/bin/sh
set -eu

docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.run_pipeline "$@"
