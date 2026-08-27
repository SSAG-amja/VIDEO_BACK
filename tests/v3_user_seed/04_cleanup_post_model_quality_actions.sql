\set ON_ERROR_STOP on
\if :{?commit_cleanup}
\else
\set commit_cleanup false
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:post-model-quality-seed'));

DELETE FROM playlists
WHERE title LIKE 'v3quality-postmodel-%'
  AND user_id IN (
      SELECT id
      FROM users
      WHERE email LIKE 'v3seed-train-%@pinlm.test'
  );

\if :commit_cleanup
COMMIT;
\echo 'Committed post-model quality action cleanup.'
\else
ROLLBACK;
\echo 'Rolled back post-model quality action cleanup dry-run.'
\endif
