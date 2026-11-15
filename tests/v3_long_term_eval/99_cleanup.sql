\set ON_ERROR_STOP on
\if :{?commit_cleanup}
\else
\set commit_cleanup false
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:long-term-eval'));

CREATE TEMP TABLE _v3_eval_cleanup_users ON COMMIT DROP AS
SELECT id
FROM users
WHERE email LIKE 'v3eval-ml-%@pinlm.test';

CREATE TEMP TABLE _v3_eval_cleanup_runs ON COMMIT DROP AS
SELECT DISTINCT recommendation.run_id
FROM ontology_recommendations AS recommendation
JOIN _v3_eval_cleanup_users AS eval_user ON eval_user.id = recommendation.user_id;

SELECT
    (SELECT count(*) FROM _v3_eval_cleanup_users) AS users_to_delete,
    (SELECT count(*) FROM _v3_eval_cleanup_runs) AS recommendation_runs_to_delete;

DELETE FROM recommendation_runs
WHERE run_id IN (SELECT run_id FROM _v3_eval_cleanup_runs);

DELETE FROM users
WHERE id IN (SELECT id FROM _v3_eval_cleanup_users);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test') THEN
        RAISE EXCEPTION 'V3 long-term evaluation users remain after cleanup';
    END IF;
END $$;

\if :commit_cleanup
COMMIT;
\echo 'Committed V3 long-term evaluation cleanup.'
\else
ROLLBACK;
\echo 'Rolled back cleanup preview. Pass -v commit_cleanup=true to delete users.'
\endif
