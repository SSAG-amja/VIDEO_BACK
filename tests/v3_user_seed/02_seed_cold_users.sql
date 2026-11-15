\set ON_ERROR_STOP on
\if :{?commit_seed}
\else
\set commit_seed false
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:user-seed'));

DO $$
BEGIN
    IF (SELECT count(*) FROM users WHERE email LIKE 'v3seed-train-%@pinlm.test' AND deleted_at IS NULL) <> 120 THEN
        RAISE EXCEPTION 'training seed must contain 120 active users before cold users are added';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM ontology_builds
        WHERE id = 22
          AND engine_name = 'v3'
          AND schema_version = 'v3.0'
          AND status = 'success'
    ) THEN
        RAISE EXCEPTION 'V3 ontology build 22 is not available';
    END IF;
END $$;

CREATE TEMP TABLE _v3_cold_cohorts (
    cohort_id integer PRIMARY KEY,
    genre_tmdb_ids integer[] NOT NULL,
    ott_tmdb_id integer NOT NULL,
    opposite_cohort_id integer NOT NULL
) ON COMMIT DROP;

INSERT INTO _v3_cold_cohorts VALUES
    (1, ARRAY[28, 80, 53], 8, 2),
    (2, ARRAY[10749, 18, 35], 1883, 3),
    (3, ARRAY[27, 9648, 53], 97, 4),
    (4, ARRAY[16, 10751, 12], 337, 3),
    (5, ARRAY[878, 14, 12], 119, 6),
    (6, ARRAY[99, 36, 10752], 356, 5);

INSERT INTO users (
    email,
    hashed_password,
    nickname,
    birth_date,
    gender,
    deleted_at,
    is_onboarding_completed
)
SELECT
    format('v3seed-cold-%s@pinlm.test', lpad(user_no::text, 3, '0')),
    '$2b$12$M2qlLa3zIFRJOf4heyIDl.g8i1bWC7fGQnKKx1A1/o3VuzlCNQbqS',
    format('v3c%s', lpad(user_no::text, 3, '0')),
    date '1987-01-01' + ((user_no * 131) % 6500),
    (ARRAY['F', 'U', 'M'])[1 + ((user_no - 1) % 3)],
    NULL,
    user_no <= 20
FROM generate_series(1, 24) AS generated(user_no)
ON CONFLICT (email) DO UPDATE SET
    hashed_password = EXCLUDED.hashed_password,
    nickname = EXCLUDED.nickname,
    birth_date = EXCLUDED.birth_date,
    gender = EXCLUDED.gender,
    deleted_at = NULL,
    is_onboarding_completed = EXCLUDED.is_onboarding_completed;

CREATE TEMP TABLE _v3_cold_users ON COMMIT DROP AS
SELECT
    generated.user_no,
    user_row.id AS user_id,
    1 + ((generated.user_no - 1) % 6) AS cohort_id,
    CASE
        WHEN generated.user_no <= 8 THEN 'genre_favorite'
        WHEN generated.user_no <= 16 THEN 'genre_only'
        WHEN generated.user_no <= 20 THEN 'ott_only'
        ELSE 'empty_profile'
    END AS profile_type
FROM generate_series(1, 24) AS generated(user_no)
JOIN users AS user_row
  ON user_row.email = format('v3seed-cold-%s@pinlm.test', lpad(generated.user_no::text, 3, '0'));

CREATE UNIQUE INDEX ON _v3_cold_users (user_id);

CREATE TEMP TABLE _v3_seed_run_ids ON COMMIT DROP AS
SELECT DISTINCT recommendation.run_id
FROM ontology_recommendations AS recommendation
JOIN _v3_cold_users AS seed_user ON seed_user.user_id = recommendation.user_id;

DELETE FROM recommendation_runs
WHERE run_id IN (SELECT run_id FROM _v3_seed_run_ids);

DELETE FROM recommendation_feed_events WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM recommendations WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM likes WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM replies WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM posts WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM playlists WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM user_interactions WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM user_favorite_movies WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM user_genres WHERE user_id IN (SELECT user_id FROM _v3_cold_users);
DELETE FROM user_otts WHERE user_id IN (SELECT user_id FROM _v3_cold_users);

INSERT INTO user_genres (user_id, genre_id)
SELECT DISTINCT seed_user.user_id, genre.id
FROM _v3_cold_users AS seed_user
JOIN _v3_cold_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
CROSS JOIN LATERAL unnest(cohort.genre_tmdb_ids) AS selected(tmdb_id)
JOIN genres AS genre ON genre.tmdb_id = selected.tmdb_id
WHERE seed_user.profile_type IN ('genre_favorite', 'genre_only');

INSERT INTO user_otts (user_id, ott_id)
SELECT seed_user.user_id, ott.id
FROM _v3_cold_users AS seed_user
JOIN _v3_cold_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
JOIN otts AS ott ON ott.tmdb_id = cohort.ott_tmdb_id
WHERE seed_user.profile_type <> 'empty_profile';

CREATE TEMP TABLE _v3_cold_movies (
    cohort_id integer NOT NULL,
    slot integer NOT NULL,
    movie_id integer NOT NULL,
    PRIMARY KEY (cohort_id, slot)
) ON COMMIT DROP;

INSERT INTO _v3_cold_movies (cohort_id, slot, movie_id)
SELECT cohort.cohort_id, ranked.slot, ranked.movie_id
FROM _v3_cold_cohorts AS cohort
CROSS JOIN LATERAL (
    SELECT
        candidate.id AS movie_id,
        row_number() OVER (
            ORDER BY candidate.vote_count DESC, candidate.popularity DESC NULLS LAST, candidate.id
        )::integer AS slot
    FROM (
        SELECT movie.id, movie.vote_count, movie.popularity
        FROM movies AS movie
        JOIN movie_genres AS movie_genre ON movie_genre.movie_id = movie.id
        JOIN genres AS genre ON genre.id = movie_genre.genre_id
        WHERE genre.tmdb_id = ANY(cohort.genre_tmdb_ids)
          AND movie.adult IS FALSE
          AND COALESCE(NULLIF(trim(movie.title_ko), ''), NULLIF(trim(movie.title), '')) IS NOT NULL
          AND COALESCE(movie.vote_count, 0) >= 20
          AND EXISTS (
              SELECT 1
              FROM ontology_nodes AS movie_node
              WHERE movie_node.build_id = 22
                AND movie_node.node_type = 'movie'
                AND movie_node.ref_id = movie.id::text
                AND movie_node.is_active IS TRUE
          )
        GROUP BY movie.id, movie.vote_count, movie.popularity
    ) AS candidate
    ORDER BY candidate.vote_count DESC, candidate.popularity DESC NULLS LAST, candidate.id
    LIMIT 20
) AS ranked;

INSERT INTO user_favorite_movies (user_id, movie_id)
SELECT seed_user.user_id, seed_movie.movie_id
FROM _v3_cold_users AS seed_user
CROSS JOIN generate_series(1, 5) AS favorite(ordinal)
JOIN _v3_cold_movies AS seed_movie
  ON seed_movie.cohort_id = seed_user.cohort_id
 AND seed_movie.slot = favorite.ordinal
WHERE seed_user.profile_type = 'genre_favorite';

CREATE TEMP TABLE _v3_mutated_training_users ON COMMIT DROP AS
SELECT
    generated.user_no,
    user_row.id AS user_id,
    generated.user_no AS cohort_id
FROM generate_series(1, 6) AS generated(user_no)
JOIN users AS user_row
  ON user_row.email = format('v3seed-train-%s@pinlm.test', lpad(generated.user_no::text, 3, '0'));

INSERT INTO user_genres (user_id, genre_id)
SELECT mutated.user_id, genre.id
FROM _v3_mutated_training_users AS mutated
JOIN _v3_cold_cohorts AS cohort ON cohort.cohort_id = mutated.cohort_id
JOIN _v3_cold_cohorts AS opposite ON opposite.cohort_id = cohort.opposite_cohort_id
JOIN genres AS genre ON genre.tmdb_id = opposite.genre_tmdb_ids[1]
ON CONFLICT DO NOTHING;

INSERT INTO user_favorite_movies (user_id, movie_id)
SELECT mutated.user_id, seed_movie.movie_id
FROM _v3_mutated_training_users AS mutated
JOIN _v3_cold_cohorts AS cohort ON cohort.cohort_id = mutated.cohort_id
JOIN _v3_cold_movies AS seed_movie
  ON seed_movie.cohort_id = cohort.opposite_cohort_id
 AND seed_movie.slot = 10
ON CONFLICT DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM _v3_cold_users) <> 24 THEN
        RAISE EXCEPTION 'expected 24 cold users';
    END IF;

    IF (SELECT count(*) FROM _v3_cold_users WHERE profile_type = 'genre_favorite') <> 8
       OR (SELECT count(*) FROM _v3_cold_users WHERE profile_type = 'genre_only') <> 8
       OR (SELECT count(*) FROM _v3_cold_users WHERE profile_type = 'ott_only') <> 4
       OR (SELECT count(*) FROM _v3_cold_users WHERE profile_type = 'empty_profile') <> 4 THEN
        RAISE EXCEPTION 'cold user profile distribution is invalid';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM _v3_cold_users AS seed_user
        JOIN user_interactions AS interaction ON interaction.user_id = seed_user.user_id
    ) THEN
        RAISE EXCEPTION 'cold users must not have home interaction rows';
    END IF;

    IF (
        SELECT count(*)
        FROM _v3_cold_users AS seed_user
        WHERE seed_user.profile_type = 'genre_favorite'
          AND (SELECT count(*) FROM user_favorite_movies AS favorite WHERE favorite.user_id = seed_user.user_id) = 5
    ) <> 8 THEN
        RAISE EXCEPTION 'genre_favorite cold users must each have five favorite movies';
    END IF;

    IF (
        SELECT count(*)
        FROM _v3_cold_users AS seed_user
        WHERE seed_user.profile_type = 'empty_profile'
          AND NOT EXISTS (SELECT 1 FROM user_genres AS mapping WHERE mapping.user_id = seed_user.user_id)
          AND NOT EXISTS (SELECT 1 FROM user_otts AS mapping WHERE mapping.user_id = seed_user.user_id)
          AND NOT EXISTS (SELECT 1 FROM user_favorite_movies AS mapping WHERE mapping.user_id = seed_user.user_id)
    ) <> 4 THEN
        RAISE EXCEPTION 'empty-profile cold users contain onboarding data';
    END IF;
END $$;

SELECT
    seed_user.profile_type,
    count(*) AS users,
    count(*) FILTER (WHERE user_row.is_onboarding_completed IS TRUE) AS onboarding_completed,
    sum((SELECT count(*) FROM user_genres AS mapping WHERE mapping.user_id = seed_user.user_id)) AS genres,
    sum((SELECT count(*) FROM user_otts AS mapping WHERE mapping.user_id = seed_user.user_id)) AS otts,
    sum((SELECT count(*) FROM user_favorite_movies AS mapping WHERE mapping.user_id = seed_user.user_id)) AS favorites
FROM _v3_cold_users AS seed_user
JOIN users AS user_row ON user_row.id = seed_user.user_id
GROUP BY seed_user.profile_type
ORDER BY seed_user.profile_type;

\if :commit_seed
COMMIT;
\echo 'Committed V3 cold seed (24 users and 6 known-user onboarding mutations).'
\else
ROLLBACK;
\echo 'Rolled back V3 cold seed dry-run.'
\endif
