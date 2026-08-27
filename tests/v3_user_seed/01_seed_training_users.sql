\set ON_ERROR_STOP on
\if :{?commit_seed}
\else
\set commit_seed false
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('pinlm:v3:user-seed'));

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM users
        WHERE email LIKE 'v3seed-cold-%@pinlm.test'
    ) THEN
        RAISE EXCEPTION 'cold seed users already exist; run Redis and SQL cleanup before rebuilding training data';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM ontology_builds
        WHERE id = 22
          AND engine_name = 'v3'
          AND schema_version = 'v3.0'
          AND status = 'success'
    ) THEN
        RAISE EXCEPTION 'V3 ontology build 22 is not available';
    END IF;

    IF (SELECT count(*) FROM genres WHERE tmdb_id IN (
        12, 14, 16, 18, 27, 28, 35, 36, 53, 80, 99, 878, 9648, 10749, 10751, 10752
    )) <> 16 THEN
        RAISE EXCEPTION 'required seed genres are missing';
    END IF;

    IF (SELECT count(*) FROM otts WHERE tmdb_id IN (8, 97, 119, 337, 356, 1883)) <> 6 THEN
        RAISE EXCEPTION 'required seed OTT providers are missing';
    END IF;
END $$;

CREATE TEMP TABLE _v3_cohorts (
    cohort_id integer PRIMARY KEY,
    cohort_name text NOT NULL,
    genre_tmdb_ids integer[] NOT NULL,
    ott_tmdb_ids integer[] NOT NULL,
    adjacent_cohort_id integer NOT NULL,
    opposite_cohort_id integer NOT NULL
) ON COMMIT DROP;

INSERT INTO _v3_cohorts VALUES
    (1, 'action_crime_thriller', ARRAY[28, 80, 53], ARRAY[8, 97], 5, 2),
    (2, 'romance_drama_comedy', ARRAY[10749, 18, 35], ARRAY[1883, 8], 1, 3),
    (3, 'horror_mystery_thriller', ARRAY[27, 9648, 53], ARRAY[97, 356], 1, 4),
    (4, 'animation_family_adventure', ARRAY[16, 10751, 12], ARRAY[337, 8], 5, 3),
    (5, 'scifi_fantasy_adventure', ARRAY[878, 14, 12], ARRAY[8, 119], 4, 6),
    (6, 'documentary_history_war', ARRAY[99, 36, 10752], ARRAY[356, 97], 2, 5);

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
    format('v3seed-train-%s@pinlm.test', lpad(user_no::text, 3, '0')),
    '$2b$12$M2qlLa3zIFRJOf4heyIDl.g8i1bWC7fGQnKKx1A1/o3VuzlCNQbqS',
    format('v3t%s', lpad(user_no::text, 3, '0')),
    date '1985-01-01' + ((user_no * 97) % 7300),
    (ARRAY['M', 'F', 'U'])[1 + ((user_no - 1) % 3)],
    NULL,
    TRUE
FROM generate_series(1, 120) AS generated(user_no)
ON CONFLICT (email) DO UPDATE SET
    hashed_password = EXCLUDED.hashed_password,
    nickname = EXCLUDED.nickname,
    birth_date = EXCLUDED.birth_date,
    gender = EXCLUDED.gender,
    deleted_at = NULL,
    is_onboarding_completed = TRUE;

CREATE TEMP TABLE _v3_training_users ON COMMIT DROP AS
SELECT
    generated.user_no,
    user_row.id AS user_id,
    1 + ((generated.user_no - 1) % 6) AS cohort_id,
    CASE
        WHEN generated.user_no <= 72 THEN 'stable'
        WHEN generated.user_no <= 96 THEN 'mixed'
        WHEN generated.user_no <= 108 THEN 'drift'
        ELSE 'negative_heavy'
    END AS profile_type
FROM generate_series(1, 120) AS generated(user_no)
JOIN users AS user_row
  ON user_row.email = format('v3seed-train-%s@pinlm.test', lpad(generated.user_no::text, 3, '0'));

CREATE UNIQUE INDEX ON _v3_training_users (user_id);
CREATE UNIQUE INDEX ON _v3_training_users (user_no);

CREATE TEMP TABLE _v3_seed_run_ids ON COMMIT DROP AS
SELECT DISTINCT recommendation.run_id
FROM ontology_recommendations AS recommendation
JOIN _v3_training_users AS seed_user ON seed_user.user_id = recommendation.user_id;

DELETE FROM recommendation_runs
WHERE run_id IN (SELECT run_id FROM _v3_seed_run_ids);

DELETE FROM recommendation_feed_events
WHERE user_id IN (SELECT user_id FROM _v3_training_users);

DELETE FROM recommendations
WHERE user_id IN (SELECT user_id FROM _v3_training_users);

DELETE FROM likes
WHERE user_id IN (SELECT user_id FROM _v3_training_users)
   OR post_id IN (
       SELECT id FROM posts WHERE user_id IN (SELECT user_id FROM _v3_training_users)
   );

DELETE FROM replies
WHERE user_id IN (SELECT user_id FROM _v3_training_users)
   OR post_id IN (
       SELECT id FROM posts WHERE user_id IN (SELECT user_id FROM _v3_training_users)
   );

DELETE FROM posts WHERE user_id IN (SELECT user_id FROM _v3_training_users);
DELETE FROM playlists WHERE user_id IN (SELECT user_id FROM _v3_training_users);
DELETE FROM user_interactions WHERE user_id IN (SELECT user_id FROM _v3_training_users);
DELETE FROM user_favorite_movies WHERE user_id IN (SELECT user_id FROM _v3_training_users);
DELETE FROM user_genres WHERE user_id IN (SELECT user_id FROM _v3_training_users);
DELETE FROM user_otts WHERE user_id IN (SELECT user_id FROM _v3_training_users);

INSERT INTO user_genres (user_id, genre_id)
SELECT DISTINCT seed_user.user_id, genre.id
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
CROSS JOIN LATERAL unnest(cohort.genre_tmdb_ids) AS selected(tmdb_id)
JOIN genres AS genre ON genre.tmdb_id = selected.tmdb_id;

INSERT INTO user_genres (user_id, genre_id)
SELECT DISTINCT seed_user.user_id, genre.id
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
JOIN _v3_cohorts AS adjacent ON adjacent.cohort_id = cohort.adjacent_cohort_id
CROSS JOIN LATERAL unnest(adjacent.genre_tmdb_ids[1:2]) AS selected(tmdb_id)
JOIN genres AS genre ON genre.tmdb_id = selected.tmdb_id
WHERE seed_user.profile_type = 'mixed'
ON CONFLICT DO NOTHING;

INSERT INTO user_otts (user_id, ott_id)
SELECT seed_user.user_id, ott.id
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
JOIN otts AS ott ON ott.tmdb_id = cohort.ott_tmdb_ids[1];

INSERT INTO user_otts (user_id, ott_id)
SELECT seed_user.user_id, ott.id
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
JOIN otts AS ott ON ott.tmdb_id = cohort.ott_tmdb_ids[2]
WHERE seed_user.user_no % 2 = 0
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE _v3_seed_movies (
    cohort_id integer NOT NULL,
    slot integer NOT NULL,
    movie_id integer NOT NULL,
    PRIMARY KEY (cohort_id, slot)
) ON COMMIT DROP;

INSERT INTO _v3_seed_movies (cohort_id, slot, movie_id)
SELECT cohort.cohort_id, ranked.slot, ranked.movie_id
FROM _v3_cohorts AS cohort
CROSS JOIN LATERAL (
    SELECT
        candidate.id AS movie_id,
        row_number() OVER (
            ORDER BY
                candidate.vote_count DESC,
                candidate.popularity DESC NULLS LAST,
                candidate.id
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
    ORDER BY
        candidate.vote_count DESC,
        candidate.popularity DESC NULLS LAST,
        candidate.id
    LIMIT 120
) AS ranked;

DO $$
DECLARE
    insufficient record;
BEGIN
    SELECT cohort_id, count(*) AS movie_count
    INTO insufficient
    FROM _v3_seed_movies
    GROUP BY cohort_id
    HAVING count(*) < 120
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'cohort % has only % seed movies', insufficient.cohort_id, insufficient.movie_count;
    END IF;
END $$;

INSERT INTO user_favorite_movies (user_id, movie_id)
WITH candidates AS (
    SELECT
        seed_user.user_id,
        seed_movie.movie_id,
        favorite.candidate_ordinal
    FROM _v3_training_users AS seed_user
    JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
    CROSS JOIN generate_series(1, 10) AS favorite(candidate_ordinal)
    JOIN _v3_seed_movies AS seed_movie
      ON seed_movie.cohort_id = CASE
          WHEN seed_user.profile_type = 'mixed' AND favorite.candidate_ordinal % 2 = 0
              THEN cohort.adjacent_cohort_id
          ELSE seed_user.cohort_id
      END
     AND seed_movie.slot = favorite.candidate_ordinal
), deduplicated AS (
    SELECT DISTINCT ON (user_id, movie_id)
        user_id,
        movie_id,
        candidate_ordinal
    FROM candidates
    ORDER BY user_id, movie_id, candidate_ordinal
), ranked AS (
    SELECT
        user_id,
        movie_id,
        row_number() OVER (
            PARTITION BY user_id ORDER BY candidate_ordinal, movie_id
        ) AS ordinal
    FROM deduplicated
)
SELECT user_id, movie_id
FROM ranked
WHERE ordinal <= 5;

INSERT INTO playlists (user_id, title, is_public)
SELECT
    seed_user.user_id,
    format(
        'v3seed-t%s-%s',
        lpad(seed_user.user_no::text, 3, '0'),
        CASE WHEN playlist_no = 1 THEN 'main' ELSE 'later' END
    ),
    playlist_no = 1
FROM _v3_training_users AS seed_user
CROSS JOIN generate_series(1, 2) AS generated(playlist_no);

CREATE TEMP TABLE _v3_interaction_plan (
    user_id integer NOT NULL,
    movie_id integer NOT NULL,
    action text NOT NULL,
    occurred_at timestamp without time zone NOT NULL,
    CHECK (action IN ('pinned', 'watched', 'passed'))
) ON COMMIT DROP;

INSERT INTO _v3_interaction_plan (user_id, movie_id, action, occurred_at)
SELECT
    seed_user.user_id,
    seed_movie.movie_id,
    'pinned',
    CASE
        WHEN seed_user.profile_type = 'drift'
            THEN now() - make_interval(days => pin.ordinal)
        ELSE now() - make_interval(days => 1 + ((seed_user.user_no * 7 + pin.ordinal * 11) % 170))
    END
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
CROSS JOIN LATERAL generate_series(
    1,
    CASE WHEN seed_user.profile_type = 'negative_heavy' THEN 4 ELSE 8 END
) AS pin(ordinal)
JOIN _v3_seed_movies AS seed_movie
  ON seed_movie.cohort_id = CASE
      WHEN seed_user.profile_type = 'drift' THEN cohort.opposite_cohort_id
      WHEN seed_user.profile_type = 'mixed' AND pin.ordinal % 2 = 0 THEN cohort.adjacent_cohort_id
      ELSE seed_user.cohort_id
  END
 AND seed_movie.slot = 6 + ((seed_user.user_no * 3 + pin.ordinal - 1) % 20);

INSERT INTO _v3_interaction_plan (user_id, movie_id, action, occurred_at)
SELECT
    seed_user.user_id,
    seed_movie.movie_id,
    'watched',
    CASE
        WHEN seed_user.profile_type = 'drift'
            THEN now() - make_interval(days => 60 + ((seed_user.user_no + watched.ordinal * 13) % 121))
        ELSE now() - make_interval(days => 7 + ((seed_user.user_no * 5 + watched.ordinal * 17) % 174))
    END
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
CROSS JOIN LATERAL generate_series(
    1,
    CASE WHEN seed_user.profile_type = 'negative_heavy' THEN 4 ELSE 8 END
) AS watched(ordinal)
JOIN _v3_seed_movies AS seed_movie
  ON seed_movie.cohort_id = CASE
      WHEN seed_user.profile_type = 'mixed' AND watched.ordinal % 2 = 0 THEN cohort.adjacent_cohort_id
      ELSE seed_user.cohort_id
  END
 AND seed_movie.slot = 26 + ((seed_user.user_no * 5 + watched.ordinal - 1) % 20);

INSERT INTO _v3_interaction_plan (user_id, movie_id, action, occurred_at)
SELECT
    seed_user.user_id,
    seed_movie.movie_id,
    'passed',
    CASE
        WHEN seed_user.profile_type IN ('drift', 'negative_heavy')
            THEN now() - make_interval(days => 1 + passed.ordinal)
        ELSE now() - make_interval(days => 5 + ((seed_user.user_no + passed.ordinal * 19) % 90))
    END
FROM _v3_training_users AS seed_user
JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
CROSS JOIN LATERAL generate_series(
    1,
    CASE WHEN seed_user.profile_type = 'negative_heavy' THEN 12 ELSE 6 END
) AS passed(ordinal)
JOIN _v3_seed_movies AS seed_movie
  ON seed_movie.cohort_id = CASE
      WHEN seed_user.profile_type = 'drift' THEN seed_user.cohort_id
      WHEN seed_user.profile_type = 'mixed' THEN 1 + (seed_user.cohort_id + 1) % 6
      ELSE cohort.opposite_cohort_id
  END
 AND seed_movie.slot = 66 + ((seed_user.user_no * 7 + passed.ordinal - 1) % 30);

CREATE TEMP TABLE _v3_saved_plan (
    user_id integer NOT NULL,
    movie_id integer NOT NULL,
    ordinal integer NOT NULL,
    saved_at timestamp without time zone NOT NULL,
    PRIMARY KEY (user_id, movie_id)
) ON COMMIT DROP;

INSERT INTO _v3_saved_plan (user_id, movie_id, ordinal, saved_at)
WITH candidates AS (
    SELECT
        seed_user.user_id,
        seed_movie.movie_id,
        saved.candidate_ordinal,
        CASE
            WHEN seed_user.profile_type = 'drift'
                THEN now() - make_interval(days => 2 + saved.candidate_ordinal)
            ELSE now() - make_interval(
                days => 2 + ((seed_user.user_no * 11 + saved.candidate_ordinal * 7) % 120)
            )
        END AS saved_at
    FROM _v3_training_users AS seed_user
    JOIN _v3_cohorts AS cohort ON cohort.cohort_id = seed_user.cohort_id
    CROSS JOIN generate_series(1, 12) AS saved(candidate_ordinal)
    JOIN _v3_seed_movies AS seed_movie
      ON seed_movie.cohort_id = CASE
          WHEN seed_user.profile_type = 'drift' THEN cohort.opposite_cohort_id
          WHEN seed_user.profile_type = 'mixed' AND saved.candidate_ordinal % 2 = 0
              THEN cohort.adjacent_cohort_id
          ELSE seed_user.cohort_id
      END
     AND seed_movie.slot = 46 + ((seed_user.user_no * 2 + saved.candidate_ordinal - 1) % 20)
), deduplicated AS (
    SELECT DISTINCT ON (user_id, movie_id)
        user_id,
        movie_id,
        candidate_ordinal,
        saved_at
    FROM candidates
    ORDER BY user_id, movie_id, candidate_ordinal
), ranked AS (
    SELECT
        user_id,
        movie_id,
        row_number() OVER (
            PARTITION BY user_id ORDER BY candidate_ordinal, movie_id
        )::integer AS ordinal,
        saved_at
    FROM deduplicated
)
SELECT user_id, movie_id, ordinal, saved_at
FROM ranked
WHERE ordinal <= 8;

DELETE FROM _v3_interaction_plan AS passed
WHERE passed.action = 'passed'
  AND (
      EXISTS (
          SELECT 1
          FROM _v3_interaction_plan AS positive
          WHERE positive.user_id = passed.user_id
            AND positive.movie_id = passed.movie_id
            AND positive.action IN ('pinned', 'watched')
      )
      OR EXISTS (
          SELECT 1
          FROM user_favorite_movies AS favorite
          WHERE favorite.user_id = passed.user_id
            AND favorite.movie_id = passed.movie_id
      )
      OR EXISTS (
          SELECT 1
          FROM _v3_saved_plan AS saved
          WHERE saved.user_id = passed.user_id
            AND saved.movie_id = passed.movie_id
      )
  );

INSERT INTO user_interactions (
    user_id,
    movie_id,
    is_pinned,
    is_watched,
    is_passed,
    pinned_at,
    watched_at,
    passed_at
)
SELECT
    plan.user_id,
    plan.movie_id,
    bool_or(plan.action = 'pinned'),
    bool_or(plan.action = 'watched'),
    bool_or(plan.action = 'passed'),
    max(plan.occurred_at) FILTER (WHERE plan.action = 'pinned'),
    max(plan.occurred_at) FILTER (WHERE plan.action = 'watched'),
    max(plan.occurred_at) FILTER (WHERE plan.action = 'passed')
FROM _v3_interaction_plan AS plan
GROUP BY plan.user_id, plan.movie_id;

INSERT INTO playlist_movies (playlist_id, movie_id, created_at)
SELECT
    playlist.id,
    saved.movie_id,
    saved.saved_at
FROM _v3_saved_plan AS saved
JOIN _v3_training_users AS seed_user ON seed_user.user_id = saved.user_id
JOIN playlists AS playlist
  ON playlist.user_id = saved.user_id
 AND playlist.title = format(
     'v3seed-t%s-%s',
     lpad(seed_user.user_no::text, 3, '0'),
     CASE WHEN saved.ordinal <= 4 THEN 'main' ELSE 'later' END
 );

CREATE TEMP TABLE _v3_community_users ON COMMIT DROP AS
SELECT * FROM _v3_training_users WHERE user_no <= 24;

INSERT INTO posts (
    user_id,
    movie_id,
    playlist_id,
    is_playlist,
    post_title,
    content,
    created_at
)
SELECT
    community.user_id,
    CASE WHEN community.user_no <= 16 THEN favorite.movie_id END,
    CASE WHEN community.user_no > 16 THEN playlist.id END,
    community.user_no > 16,
    format('V3 seed post %s', lpad(community.user_no::text, 3, '0')),
    format('Deterministic V3 community seed content for user %s.', community.user_no),
    now() - make_interval(hours => 30 - community.user_no)
FROM _v3_community_users AS community
LEFT JOIN LATERAL (
    SELECT mapping.movie_id
    FROM user_favorite_movies AS mapping
    WHERE mapping.user_id = community.user_id
    ORDER BY mapping.movie_id
    LIMIT 1
) AS favorite ON TRUE
LEFT JOIN playlists AS playlist
  ON playlist.user_id = community.user_id
 AND playlist.title = format('v3seed-t%s-main', lpad(community.user_no::text, 3, '0'));

CREATE TEMP TABLE _v3_community_posts ON COMMIT DROP AS
SELECT
    community.user_no,
    community.user_id,
    post.id AS post_id,
    post.created_at
FROM _v3_community_users AS community
JOIN posts AS post
  ON post.user_id = community.user_id
 AND post.post_title = format('V3 seed post %s', lpad(community.user_no::text, 3, '0'));

INSERT INTO likes (user_id, post_id)
SELECT source.user_id, target.post_id
FROM _v3_community_posts AS source
CROSS JOIN generate_series(1, 4) AS liked(offset_no)
JOIN _v3_community_posts AS target
  ON target.user_no = 1 + ((source.user_no + liked.offset_no - 1) % 24)
WHERE source.user_id <> target.user_id
ON CONFLICT DO NOTHING;

INSERT INTO replies (user_id, post_id, content, created_at)
SELECT
    source.user_id,
    target.post_id,
    format('V3 seed reply %s-%s', source.user_no, replied.offset_no),
    GREATEST(target.created_at + interval '30 minutes', now() - make_interval(mins => source.user_no))
FROM _v3_community_posts AS source
CROSS JOIN generate_series(1, 2) AS replied(offset_no)
JOIN _v3_community_posts AS target
  ON target.user_no = 1 + ((source.user_no + replied.offset_no * 3 - 1) % 24)
WHERE source.user_id <> target.user_id;

DO $$
DECLARE
    training_user_count integer;
    insufficient_positive_users integer;
BEGIN
    SELECT count(*) INTO training_user_count FROM _v3_training_users;
    IF training_user_count <> 120 THEN
        RAISE EXCEPTION 'expected 120 training users, found %', training_user_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM user_interactions AS interaction
        JOIN _v3_training_users AS seed_user ON seed_user.user_id = interaction.user_id
        WHERE interaction.is_pinned IS TRUE AND interaction.is_passed IS TRUE
    ) THEN
        RAISE EXCEPTION 'seed contains a pinned/passed state conflict';
    END IF;

    SELECT count(*) INTO insufficient_positive_users
    FROM _v3_training_users AS seed_user
    WHERE (
        SELECT count(DISTINCT movie_id)
        FROM (
            SELECT favorite.movie_id
            FROM user_favorite_movies AS favorite
            WHERE favorite.user_id = seed_user.user_id
            UNION
            SELECT interaction.movie_id
            FROM user_interactions AS interaction
            WHERE interaction.user_id = seed_user.user_id
              AND (interaction.is_pinned IS TRUE OR interaction.is_watched IS TRUE)
            UNION
            SELECT playlist_movie.movie_id
            FROM playlists AS playlist
            JOIN playlist_movies AS playlist_movie ON playlist_movie.playlist_id = playlist.id
            WHERE playlist.user_id = seed_user.user_id
        ) AS positive_movie
    ) < 12;

    IF insufficient_positive_users <> 0 THEN
        RAISE EXCEPTION '% training users have fewer than 12 positive movies', insufficient_positive_users;
    END IF;

    IF (SELECT count(*) FROM playlists WHERE user_id IN (SELECT user_id FROM _v3_training_users)) <> 240 THEN
        RAISE EXCEPTION 'expected 240 training playlists';
    END IF;

    IF (SELECT count(*) FROM posts WHERE user_id IN (SELECT user_id FROM _v3_training_users)) <> 24 THEN
        RAISE EXCEPTION 'expected 24 community posts';
    END IF;

    IF (SELECT count(*) FROM likes WHERE user_id IN (SELECT user_id FROM _v3_community_users)) <> 96 THEN
        RAISE EXCEPTION 'expected 96 community likes';
    END IF;

    IF (SELECT count(*) FROM replies WHERE user_id IN (SELECT user_id FROM _v3_community_users)) <> 48 THEN
        RAISE EXCEPTION 'expected 48 community replies';
    END IF;
END $$;

SELECT
    seed_user.profile_type,
    count(*) AS users,
    sum((SELECT count(*) FROM user_favorite_movies AS favorite WHERE favorite.user_id = seed_user.user_id)) AS favorites,
    sum((SELECT count(*) FROM playlists AS playlist JOIN playlist_movies AS item ON item.playlist_id = playlist.id WHERE playlist.user_id = seed_user.user_id)) AS saved,
    sum((SELECT count(*) FROM user_interactions AS interaction WHERE interaction.user_id = seed_user.user_id AND interaction.is_pinned IS TRUE)) AS pinned,
    sum((SELECT count(*) FROM user_interactions AS interaction WHERE interaction.user_id = seed_user.user_id AND interaction.is_watched IS TRUE)) AS watched,
    sum((SELECT count(*) FROM user_interactions AS interaction WHERE interaction.user_id = seed_user.user_id AND interaction.is_passed IS TRUE)) AS passed
FROM _v3_training_users AS seed_user
GROUP BY seed_user.profile_type
ORDER BY seed_user.profile_type;

\if :commit_seed
COMMIT;
\echo 'Committed V3 training seed (120 users).'
\else
ROLLBACK;
\echo 'Rolled back V3 training seed dry-run.'
\endif
