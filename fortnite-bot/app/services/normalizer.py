"""Normalizer service.

Converts each collector's raw dict output into a unified ``RawItem`` schema.

Source-level rules from the spec:
    Level 1 (official): fortnite.com/news, official YouTube
    Level 2 (API/database): fortnite-api.com, fortnite.gg
    Level 3 (leak/datamine): X (HYPEX/ShiinaBR/FireMonkey), Reddit r/FortniteLeaks
    Level 4 (trend): not implemented yet
"""

from datetime import datetime
from typing import Optional
from app.schemas import RawItem
import logging

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> Optional[datetime]:
    """Parse ISO datetime string, return None on failure."""
    if not value:
        return None
    try:
        # Handle "Z" suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def normalize_fortnite_api(item: dict) -> RawItem | None:
    """Normalize fortnite-api.com items (shop, news, new cosmetics)."""
    source = item.get("_source", "fortnite-api")
    name = item.get("name") or item.get("title") or ""
    if not name:
        return None

    # Determine category
    if "shop" in source:
        category = "item_shop"
    elif "news" in source:
        category = "official_news"
    elif "cosmetics" in source:
        category = "skin_leak"
    else:
        category = "general"

    # Build a stable URL (since fortnite-api items don't have direct URLs)
    item_id = item.get("id", "")
    url = item.get("url") or f"https://fortnite-api.com/items/{name.replace(' ', '-')}"

    image_url = ""
    images = item.get("images") or item.get("image", "")
    if isinstance(images, dict):
        image_url = images.get("icon") or images.get("featured") or ""
    elif isinstance(images, str):
        image_url = images

    return RawItem(
        source=source,
        source_level=2,
        title=name,
        url=url,
        content=item.get("description") or item.get("body") or "",
        image_url=image_url,
        category=category,
        published_at=_parse_dt(item.get("_fetched_at")),
        is_official=False,  # data-based, not officially announced
        is_leak=False,
    )


def normalize_fortnite_news(item: dict) -> RawItem | None:
    """Normalize fortnite.com/news items."""
    title = item.get("title", "")
    url = item.get("url", "")
    if not title or not url:
        return None
    return RawItem(
        source=item.get("_source", "fortnite.com/news"),
        source_level=1,
        title=title,
        url=url,
        content=item.get("content", ""),
        image_url=item.get("image_url", ""),
        category="official_news",
        published_at=_parse_dt(item.get("published_at")) or _parse_dt(item.get("_fetched_at")),
        is_official=True,
        is_leak=False,
    )


def normalize_youtube(item: dict) -> RawItem | None:
    """Normalize YouTube RSS items."""
    title = item.get("title", "")
    url = item.get("url", "")
    if not title or not url:
        return None
    return RawItem(
        source=item.get("_source", "youtube/fortnite"),
        source_level=1,
        title=title,
        url=url,
        content=item.get("content", ""),
        image_url=item.get("image_url", ""),
        category="official_news",
        published_at=_parse_dt(item.get("published_at")),
        is_official=True,
        is_leak=False,
    )


def normalize_leak_x(item: dict) -> RawItem | None:
    """Normalize X/Twitter leak items (HYPEX, ShiinaBR, FireMonkey)."""
    title = item.get("title", "")
    url = item.get("url", "")
    if not title or not url:
        return None
    return RawItem(
        source=item.get("_source", "x.com"),
        source_level=3,
        title=title,
        url=url,
        content=item.get("content", ""),
        image_url=item.get("image_url", ""),
        category="skin_leak",
        published_at=_parse_dt(item.get("published_at")),
        is_official=False,
        is_leak=True,
    )


def normalize_reddit(item: dict) -> RawItem | None:
    """Normalize Reddit r/FortniteLeaks items."""
    title = item.get("title", "")
    url = item.get("url", "")
    if not title or not url:
        return None
    return RawItem(
        source=item.get("_source", "reddit/FortniteLeaks"),
        source_level=3,
        title=title,
        url=url,
        content=item.get("content", ""),
        image_url=item.get("image_url", ""),
        category="leak_discussion",
        published_at=_parse_dt(item.get("published_at")),
        is_official=False,
        is_leak=True,
    )


def normalize_fortnite_gg(item: dict) -> RawItem | None:
    """Normalize fortnite.gg items."""
    title = item.get("title", "")
    url = item.get("url", "")
    if not title or not url:
        return None
    category = item.get("category", "leaks")
    return RawItem(
        source=item.get("_source", "fortnite.gg"),
        source_level=2,
        title=title,
        url=url,
        content=item.get("content", ""),
        image_url=item.get("image_url", ""),
        category="upcoming_skin" if category == "unreleased" else "skin_leak",
        published_at=_parse_dt(item.get("_fetched_at")),
        is_official=False,
        is_leak=True,  # fortnite.gg shows datamined items
    )


# Convenience: dispatch by source
def normalize(item: dict) -> RawItem | None:
    """Dispatch to appropriate normalizer based on _source field."""
    source = item.get("_source", "")
    if "fortnite-api" in source:
        return normalize_fortnite_api(item)
    elif "fortnite.com/news" in source:
        return normalize_fortnite_news(item)
    elif "youtube" in source:
        return normalize_youtube(item)
    elif "x.com/" in source:
        return normalize_leak_x(item)
    elif "reddit" in source:
        return normalize_reddit(item)
    elif "fortnite.gg" in source:
        return normalize_fortnite_gg(item)
    else:
        logger.warning("Unknown source: %s", source)
        return None
