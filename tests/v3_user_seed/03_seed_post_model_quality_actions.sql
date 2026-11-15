\set ON_ERROR_STOP on
\if :{?commit_seed}
\else
\set commit_seed false
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:post-model-quality-seed'));

CREATE TEMP TABLE _v3_quality_cohorts (
    cohort_id integer PRIMARY KEY,
    cohort_name text NOT NULL,
    genre_tmdb_ids integer[] NOT NULL,
    opposite_cohort_id integer NOT NULL
) ON COMMIT DROP;

INSERT INTO _v3_quality_cohorts VALUES
    (1, 'action_crime_thriller', ARRAY[28, 80, 53], 2),
    (2, 'romance_drama_comedy', ARRAY[10749, 18, 35], 3),
    (3, 'horror_mystery_thriller', ARRAY[27, 9648, 53], 4),
    (4, 'animation_family_adventure', ARRAY[16, 10751, 12], 3),
    (5, 'scifi_fantasy_adventure', ARRAY[878, 14, 12], 6),
    (6, 'documentary_history_war', ARRAY[99, 36, 10752], 5);

CREATE TEMP TABLE _v3_quality_user_plan (
    user_no integer PRIMARY KEY,
    scenario_type text NOT NULL,
    user_id integer,
    baseline_cohort_id integer,
    recent_cohort_id integer,
    CHECK (scenario_type IN ('post_model_stable', 'post_model_drift'))
) ON COMMIT DROP;

INSERT INTO _v3_quality_user_plan (user_no, scenario_type) VALUES
    (25, 'post_model_stable'),
    (26, 'post_model_stable'),
    (27, 'post_model_stable'),
    (28, 'post_model_stable'),
    (29, 'post_model_stable'),
    (30, 'post_model_stable'),
    (37, 'post_model_drift'),
    (38, 'post_model_drift'),
    (39, 'post_model_drift'),
    (58, 'post_model_drift'),
    (35, 'post_model_drift'),
    (36, 'post_model_drift');

UPDATE _v3_quality_user_plan AS plan
SET user_id = user_row.id,
    baseline_cohort_id = 1 + ((plan.user_no - 1) % 6)
FROM users AS user_row
WHERE user_row.email = format(
    'v3seed-train-%s@pinlm.test',
    lpad(plan.user_no::text, 3, '0')
);

UPDATE _v3_quality_user_plan AS plan
SET recent_cohort_id = CASE
    WHEN plan.scenario_type = 'post_model_stable' THEN plan.baseline_cohort_id
    ELSE cohort.opposite_cohort_id
END
FROM _v3_quality_cohorts AS cohort
WHERE cohort.cohort_id = plan.baseline_cohort_id;

DO $$
BEGIN
    IF (SELECT count(*) FROM _v3_quality_user_plan WHERE user_id IS NOT NULL) <> 12 THEN
        RAISE EXCEPTION 'all 12 post-model quality users must exist';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM _v3_quality_user_plan AS plan
        WHERE (
            SELECT count(*)
            FROM recommendations AS recommendation
            WHERE recommendation.user_id = plan.user_id
              AND recommendation.source = 'lightfm_v3'
        ) < 100
    ) THEN
        RAISE EXCEPTION 'post-model quality users require fixed LightFM candidate snapshots';
    END IF;
END $$;

DELETE FROM playlists AS playlist
USING _v3_quality_user_plan AS plan
WHERE playlist.user_id = plan.user_id
  AND playlist.title LIKE 'v3quality-postmodel-%';

CREATE TEMP TABLE _v3_quality_movie_pool (
    cohort_id integer NOT NULL,
    slot integer NOT NULL,
    movie_id integer NOT NULL,
    target_genre_count integer NOT NULL,
    total_genre_count integer NOT NULL,
    PRIMARY KEY (cohort_id, slot)
) ON COMMIT DROP;

INSERT INTO _v3_quality_movie_pool (
    cohort_id,
    slot,
    movie_id,
    target_genre_count,
    total_genre_count
)
SELECT
    cohort.cohort_id,
    ranked.slot,
    ranked.movie_id,
    ranked.target_genre_count,
    ranked.total_genre_count
FROM _v3_quality_cohorts AS cohort
CROSS JOIN LATERAL (
    SELECT
        candidate.movie_id,
        candidate.target_genre_count,
        candidate.total_genre_count,
        row_number() OVER (
            ORDER BY
                candidate.target_genre_count DESC,
                candidate.target_genre_count::numeric / candidate.total_genre_count DESC,
                candidate.vote_count DESC,
                candidate.popularity DESC NULLS LAST,
                candidate.movie_id
        )::integer AS slot
    FROM (
        SELECT
            movie.id AS movie_id,
            movie.vote_count,
            movie.popularity,
            count(*) FILTER (
                WHERE genre.tmdb_id = ANY(cohort.genre_tmdb_ids)
            )::integer AS target_genre_count,
            count(*)::integer AS total_genre_count
        FROM movies AS movie
        JOIN movie_genres AS movie_genre ON movie_genre.movie_id = movie.id
        JOIN genres AS genre ON genre.id = movie_genre.genre_id
        WHERE movie.adult IS FALSE
          AND COALESCE(NULLIF(trim(movie.title_ko), ''), NULLIF(trim(movie.title), '')) IS NOT NULL
          AND COALESCE(movie.vote_count, 0) >= 100
          AND EXISTS (
              SELECT 1
              FROM ontology_nodes AS movie_node
              WHERE movie_node.build_id = 22
                AND movie_node.node_type = 'movie'
                AND movie_node.ref_id = movie.id::text
                AND movie_node.is_active IS TRUE
          )
        GROUP BY movie.id, movie.vote_count, movie.popularity
        HAVING count(*) FILTER (
                   WHERE genre.tmdb_id = ANY(cohort.genre_tmdb_ids)
               ) > 0
           AND count(*) FILTER (
                   WHERE genre.tmdb_id = ANY(cohort.genre_tmdb_ids)
               )::numeric / count(*) >= 0.5
    ) AS candidate
    ORDER BY
        candidate.target_genre_count DESC,
        candidate.target_genre_count::numeric / candidate.total_genre_count DESC,
        candidate.vote_count DESC,
        candidate.popularity DESC NULLS LAST,
        candidate.movie_id
    LIMIT 120
) AS ranked;

DO $$
DECLARE
    insufficient record;
BEGIN
    SELECT cohort_id, count(*) AS movie_count
    INTO insufficient
    FROM _v3_quality_movie_pool
    GROUP BY cohort_id
    HAVING count(*) < 120
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'quality cohort % has only % movies', insufficient.cohort_id, insufficient.movie_count;
    END IF;
END $$;

INSERT INTO playlists (user_id, title, is_public)
SELECT
    plan.user_id,
    format(
        'v3quality-postmodel-%s-%s',
        CASE WHEN plan.scenario_type = 'post_model_stable' THEN 'stable' ELSE 'drift' END,
        lpad(plan.user_no::text, 3, '0')
    ),
    false
FROM _v3_quality_user_plan AS plan;

CREATE TEMP TABLE _v3_quality_action_plan (
    user_id integer NOT NULL,
    playlist_id integer NOT NULL,
    movie_id integer NOT NULL,
    ordinal integer NOT NULL,
    PRIMARY KEY (user_id, movie_id)
) ON COMMIT DROP;

INSERT INTO _v3_quality_action_plan (user_id, playlist_id, movie_id, ordinal)
SELECT
    plan.user_id,
    playlist.id,
    selected.movie_id,
    selected.ordinal
FROM _v3_quality_user_plan AS plan
JOIN playlists AS playlist
  ON playlist.user_id = plan.user_id
 AND playlist.title = format(
     'v3quality-postmodel-%s-%s',
     CASE WHEN plan.scenario_type = 'post_model_stable' THEN 'stable' ELSE 'drift' END,
     lpad(plan.user_no::text, 3, '0')
 )
CROSS JOIN LATERAL (
    SELECT
        pool.movie_id,
        row_number() OVER (ORDER BY pool.slot)::integer AS ordinal
    FROM _v3_quality_movie_pool AS pool
    WHERE pool.cohort_id = plan.recent_cohort_id
      AND NOT EXISTS (
          SELECT 1
          FROM user_favorite_movies AS favorite
          WHERE favorite.user_id = plan.user_id
            AND favorite.movie_id = pool.movie_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM user_interactions AS interaction
          WHERE interaction.user_id = plan.user_id
            AND interaction.movie_id = pool.movie_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM playlists AS existing_playlist
          JOIN playlist_movies AS existing_movie
            ON existing_movie.playlist_id = existing_playlist.id
          WHERE existing_playlist.user_id = plan.user_id
            AND existing_movie.movie_id = pool.movie_id
      )
    ORDER BY pool.slot
    LIMIT 6
) AS selected;

DO $$
DECLARE
    insufficient record;
BEGIN
    SELECT plan.user_no, count(action.movie_id) AS action_count
    INTO insufficient
    FROM _v3_quality_user_plan AS plan
    LEFT JOIN _v3_quality_action_plan AS action ON action.user_id = plan.user_id
    GROUP BY plan.user_no
    HAVING count(action.movie_id) <> 6
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'quality user % has % planned actions', insufficient.user_no, insufficient.action_count;
    END IF;
END $$;

INSERT INTO playlist_movies (playlist_id, movie_id, created_at)
SELECT
    action.playlist_id,
    action.movie_id,
    now() - make_interval(hours => action.ordinal * 2)
FROM _v3_quality_action_plan AS action;

\if :commit_seed
COMMIT;
\echo 'Committed 72 post-model quality actions for 12 known users.'
\else
ROLLBACK;
\echo 'Rolled back post-model quality action dry-run.'
\endif
