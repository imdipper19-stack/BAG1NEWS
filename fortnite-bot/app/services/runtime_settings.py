"""Runtime-mutable settings stored in Redis.

The values in ``app.config.settings`` are loaded once from ``.env`` at
process start. We layer this module on top so the admin can adjust
common knobs (daily post limit, score threshold) live from Telegram
without rebuilding containers.

Reads fall back to ``.env`` when Redis has no override; writes always go
to Redis. This keeps ``.env`` as the source of truth for first-time
deployments.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

KEY_MAX_POSTS = "fortnite_bot:cfg:max_posts_per_day"
KEY_MIN_SCORE = "fortnite_bot:cfg:min_score_to_publish"


def _client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def get_max_posts_per_day() -> int:
    """Return the current daily post cap. Redis override → .env default."""
    r = _client()
    try:
        raw = await r.get(KEY_MAX_POSTS)
    finally:
        await r.aclose()
    if raw is None:
        return settings.max_posts_per_day
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return settings.max_posts_per_day


async def set_max_posts_per_day(value: int) -> int:
    """Persist a new daily cap. Returns the value actually stored."""
    value = max(1, min(int(value), 100))
    r = _client()
    try:
        await r.set(KEY_MAX_POSTS, value)
    finally:
        await r.aclose()
    logger.info("max_posts_per_day set to %d (via /limit)", value)
    return value


async def get_min_score_to_publish() -> int:
    """Return the current minimum score required to publish."""
    r = _client()
    try:
        raw = await r.get(KEY_MIN_SCORE)
    finally:
        await r.aclose()
    if raw is None:
        return settings.min_score_to_publish
    try:
        return max(0, min(int(raw), 100))
    except (TypeError, ValueError):
        return settings.min_score_to_publish


async def set_min_score_to_publish(value: int) -> int:
    value = max(0, min(int(value), 100))
    r = _client()
    try:
        await r.set(KEY_MIN_SCORE, value)
    finally:
        await r.aclose()
    logger.info("min_score_to_publish set to %d (via /score)", value)
    return value


async def reset_to_env() -> None:
    """Drop all Redis overrides — bot falls back to .env defaults."""
    r = _client()
    try:
        await r.delete(KEY_MAX_POSTS, KEY_MIN_SCORE)
    finally:
        await r.aclose()
