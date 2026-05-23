"""Duplicate detection service.

Two-layer dedup:
  1. Redis cache: URL hash + title hash with 7-day TTL (fast)
  2. PostgreSQL raw_items.url unique constraint (durable)

Items are deduped before scoring/writing to avoid double-publishing.
"""

import hashlib
import logging
import re
from typing import Optional

import redis.asyncio as redis
from sqlalchemy import select

from app.config import settings
from app.db.models import RawItem as RawItemORM
from app.db.session import get_session
from app.schemas import RawItem

logger = logging.getLogger(__name__)

URL_KEY_PREFIX = "fortnite_bot:dedup:url"
TITLE_KEY_PREFIX = "fortnite_bot:dedup:title"
TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def generate_content_hash(text: str) -> str:
    """SHA256 of normalized text. Used for URL and title hashes."""
    if not text:
        return ""
    # Normalize: lowercase, collapse whitespace, strip punctuation
    normalized = re.sub(r"[^\w\u0400-\u04FF\s]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class DuplicateChecker:
    """Detects duplicate news items using Redis (cache) + Postgres (durable)."""

    def __init__(self, redis_url: Optional[str] = None):
        self._redis_url = redis_url or settings.redis_url
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def is_duplicate(self, item: RawItem) -> bool:
        """Return True if this item has been seen before.

        Checks (in order):
          1. Redis URL hash
          2. Redis title hash
          3. PostgreSQL raw_items table by URL
        """
        url_hash = generate_content_hash(item.url)
        title_hash = generate_content_hash(item.title)

        client = await self._get_client()
        try:
            if url_hash and await client.exists(f"{URL_KEY_PREFIX}:{url_hash}"):
                return True
            if title_hash and await client.exists(f"{TITLE_KEY_PREFIX}:{title_hash}"):
                return True
        except Exception as e:
            logger.warning("Redis dedup check failed, falling back to DB: %s", e)

        # Postgres fallback / secondary check
        if item.url:
            try:
                async with get_session() as session:
                    stmt = select(RawItemORM.id).where(RawItemORM.url == item.url).limit(1)
                    result = await session.execute(stmt)
                    if result.scalar_one_or_none() is not None:
                        return True
            except Exception as e:
                logger.warning("Postgres dedup check failed: %s", e)

        return False

    async def mark_as_seen(self, item: RawItem) -> None:
        """Mark this item's URL and title as seen for the next 7 days."""
        url_hash = generate_content_hash(item.url)
        title_hash = generate_content_hash(item.title)

        client = await self._get_client()
        try:
            if url_hash:
                await client.setex(f"{URL_KEY_PREFIX}:{url_hash}", TTL_SECONDS, "1")
            if title_hash:
                await client.setex(f"{TITLE_KEY_PREFIX}:{title_hash}", TTL_SECONDS, "1")
        except Exception as e:
            logger.warning("Failed to mark item as seen in Redis: %s", e)

    async def filter_duplicates(self, items: list[RawItem]) -> list[RawItem]:
        """Return only items that haven't been seen before. Marks survivors as seen."""
        unique: list[RawItem] = []
        for item in items:
            if not await self.is_duplicate(item):
                unique.append(item)
                await self.mark_as_seen(item)
        return unique
