from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import SessionLocal
from app.schemas.recsys import InteractionAction, InteractionCreate
from app.services.recsys.v1.interaction_cache import (
    get_blacklisted_movie_ids,
    record_interaction_cache,
)
from app.services.recsys.profile_change import (
    V3_SHORT_TERM_PROCESSING_USERS_KEY,
    V3_SHORT_TERM_SCHEDULED_USERS_KEY,
    enqueue_recommendation_profile_refresh,
    get_recommendation_profile_version,
    profile_version_key,
    short_term_pending_meta_key,
    short_term_pending_movies_key,
    short_term_pending_weights_key,
    short_term_revision_key,
)
from app.services.recsys.v3.retrieval.short_term_candidate_cache import short_term_candidate_cache_key


TEST_EMAIL_PATTERNS = (
    "v3seed-train-%@pinlm.test",
    "v3seed-cold-%@pinlm.test",
)


@dataclass(frozen=True, slots=True)
class CachedAction:
    user_id: int
    movie_id: int
    action: InteractionAction
    occurred_at: datetime

    @property
    def encoded(self) -> str:
        return f"{self.action.value}:{self.movie_id}"


def blacklist_key(user_id: int) -> str:
    return f"user:{user_id}:movie:blacklist"


def recent_action_key(user_id: int) -> str:
    return f"user:{user_id}:recent_actions"


def load_seed_user_ids() -> tuple[int, ...]:
    statement = text(
        """
        SELECT id
        FROM users
        WHERE email LIKE :training_pattern
           OR email LIKE :cold_pattern
        ORDER BY id
        """
    )
    with SessionLocal() as db:
        values = db.execute(
            statement,
            {
                "training_pattern": TEST_EMAIL_PATTERNS[0],
                "cold_pattern": TEST_EMAIL_PATTERNS[1],
            },
        ).scalars()
        return tuple(int(value) for value in values)


def load_post_model_quality_user_ids() -> tuple[int, ...]:
    statement = text(
        """
        SELECT DISTINCT playlist.user_id
        FROM playlists AS playlist
        JOIN users AS user_row ON user_row.id = playlist.user_id
        WHERE playlist.title LIKE 'v3quality-postmodel-%'
          AND user_row.email LIKE :training_pattern
        ORDER BY playlist.user_id
        """
    )
    with SessionLocal() as db:
        values = db.execute(
            statement,
            {"training_pattern": TEST_EMAIL_PATTERNS[0]},
        ).scalars()
        return tuple(int(value) for value in values)


def load_current_actions() -> tuple[CachedAction, ...]:
    statement = text(
        """
        SELECT action.user_id, action.movie_id, action.action, action.occurred_at
        FROM (
            SELECT interaction.user_id, interaction.movie_id,
                   'pinned'::text AS action, interaction.pinned_at AS occurred_at
            FROM user_interactions AS interaction
            JOIN users AS user_row ON user_row.id = interaction.user_id
            WHERE interaction.is_pinned IS TRUE
              AND (user_row.email LIKE :training_pattern OR user_row.email LIKE :cold_pattern)
            UNION ALL
            SELECT interaction.user_id, interaction.movie_id,
                   'watched'::text AS action, interaction.watched_at AS occurred_at
            FROM user_interactions AS interaction
            JOIN users AS user_row ON user_row.id = interaction.user_id
            WHERE interaction.is_watched IS TRUE
              AND (user_row.email LIKE :training_pattern OR user_row.email LIKE :cold_pattern)
            UNION ALL
            SELECT interaction.user_id, interaction.movie_id,
                   'passed'::text AS action, interaction.passed_at AS occurred_at
            FROM user_interactions AS interaction
            JOIN users AS user_row ON user_row.id = interaction.user_id
            WHERE interaction.is_passed IS TRUE
              AND (user_row.email LIKE :training_pattern OR user_row.email LIKE :cold_pattern)
            UNION ALL
            SELECT playlist.user_id, playlist_movie.movie_id,
                   'saved'::text AS action, playlist_movie.created_at AS occurred_at
            FROM playlist_movies AS playlist_movie
            JOIN playlists AS playlist ON playlist.id = playlist_movie.playlist_id
            JOIN users AS user_row ON user_row.id = playlist.user_id
            WHERE user_row.email LIKE :training_pattern OR user_row.email LIKE :cold_pattern
        ) AS action
        WHERE action.occurred_at IS NOT NULL
        ORDER BY action.user_id, action.occurred_at, action.movie_id, action.action
        """
    )
    with SessionLocal() as db:
        rows = db.execute(
            statement,
            {
                "training_pattern": TEST_EMAIL_PATTERNS[0],
                "cold_pattern": TEST_EMAIL_PATTERNS[1],
            },
        )
        return tuple(
            CachedAction(
                user_id=int(row.user_id),
                movie_id=int(row.movie_id),
                action=InteractionAction(row.action),
                occurred_at=row.occurred_at,
            )
            for row in rows
        )


def clear_seed_cache(user_ids: tuple[int, ...]) -> int:
    redis = get_redis()
    keys = [
        key
        for user_id in user_ids
        for key in (
            blacklist_key(user_id),
            recent_action_key(user_id),
            profile_version_key(user_id),
            short_term_revision_key(user_id),
            short_term_pending_movies_key(user_id),
            short_term_pending_weights_key(user_id),
            short_term_pending_meta_key(user_id),
            short_term_candidate_cache_key(user_id),
        )
    ]
    deleted = int(redis.unlink(*keys)) if keys else 0
    if user_ids:
        redis.srem("recsys:v3:short_term:dirty_users", *user_ids)
        redis.zrem(V3_SHORT_TERM_SCHEDULED_USERS_KEY, *user_ids)
        redis.zrem(V3_SHORT_TERM_PROCESSING_USERS_KEY, *user_ids)
    return deleted


def hydrate_and_validate(*, selected_user_ids: tuple[int, ...] | None = None) -> dict:
    redis = get_redis()
    redis.ping()
    user_ids = selected_user_ids if selected_user_ids is not None else load_seed_user_ids()
    if not user_ids:
        raise RuntimeError("no V3 seed users exist")
    selected = set(user_ids)
    actions = tuple(
        action for action in load_current_actions() if action.user_id in selected
    )
    clear_seed_cache(user_ids)

    actions_by_user: dict[int, list[CachedAction]] = {user_id: [] for user_id in user_ids}
    for action in actions:
        actions_by_user[action.user_id].append(action)
        success = record_interaction_cache(
            redis,
            settings,
            InteractionCreate(
                user_id=action.user_id,
                movie_id=action.movie_id,
                action=action.action,
            ),
            occurred_at=action.occurred_at,
        )
        if not success:
            raise RuntimeError(f"Redis hydration failed user_id={action.user_id}")

    positive_actions = {InteractionAction.PINNED, InteractionAction.SAVED, InteractionAction.WATCHED}
    refresh_user_ids = tuple(
        user_id
        for user_id, user_actions in actions_by_user.items()
        if any(action.action in positive_actions for action in user_actions)
    )
    for user_id in refresh_user_ids:
        if not enqueue_recommendation_profile_refresh(redis, user_id, force=True):
            raise RuntimeError(f"short-term refresh enqueue failed user_id={user_id}")

    blacklist_count = 0
    recent_action_count = 0
    scheduled_user_count = 0
    for user_id, user_actions in actions_by_user.items():
        expected_recent = [
            action.encoded
            for action in reversed(user_actions)
        ][: settings.REDIS_RECENT_ACTION_LIMIT]
        actual_recent = redis.lrange(recent_action_key(user_id), 0, -1)
        if actual_recent != expected_recent:
            raise RuntimeError(f"recent action mismatch user_id={user_id}")

        expected_blacklist = {
            action.movie_id
            for action in user_actions
            if action.action in {InteractionAction.PASSED, InteractionAction.WATCHED}
        }
        actual_blacklist = get_blacklisted_movie_ids(redis, user_id)
        if actual_blacklist != expected_blacklist:
            raise RuntimeError(f"blacklist mismatch user_id={user_id}")
        if expected_blacklist and redis.ttl(blacklist_key(user_id)) <= 0:
            raise RuntimeError(f"blacklist TTL is missing user_id={user_id}")

        actual_version = get_recommendation_profile_version(redis, user_id)
        if actual_version != len(user_actions):
            raise RuntimeError(
                f"profile version mismatch user_id={user_id} "
                f"expected={len(user_actions)} actual={actual_version}"
            )
        is_scheduled = redis.zscore(V3_SHORT_TERM_SCHEDULED_USERS_KEY, user_id) is not None
        if is_scheduled != (user_id in refresh_user_ids):
            raise RuntimeError(f"scheduled-user membership mismatch user_id={user_id}")

        blacklist_count += len(actual_blacklist)
        recent_action_count += len(actual_recent)
        scheduled_user_count += int(is_scheduled)

    return {
        "status": "ok",
        "seed_user_count": len(user_ids),
        "db_current_action_count": len(actions),
        "redis_recent_action_count": recent_action_count,
        "redis_blacklist_movie_count": blacklist_count,
        "redis_scheduled_user_count": scheduled_user_count,
        "recent_actions_consumed_by_v3": "positive accumulation and refresh trigger",
        "blacklist_consumed_by_v3": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hydrate V3 seed Redis state from current DB actions")
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove only V3 seed user recent-action and blacklist keys",
    )
    parser.add_argument(
        "--quality-post-model",
        action="store_true",
        help="Hydrate only users marked by v3quality-postmodel playlists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected_user_ids = (
        load_post_model_quality_user_ids()
        if args.quality_post_model
        else None
    )
    if args.cleanup:
        user_ids = selected_user_ids if selected_user_ids is not None else load_seed_user_ids()
        deleted_keys = clear_seed_cache(user_ids)
        result = {
            "status": "ok",
            "seed_user_count": len(user_ids),
            "deleted_redis_key_count": deleted_keys,
        }
    else:
        result = hydrate_and_validate(selected_user_ids=selected_user_ids)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
