import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

# Multiple nitter mirrors - try them in order until one works
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.kavin.rocks",
    "https://nitter.cz",
]

# Twitter/X accounts that publish Fortnite leaks/dataminings
LEAK_ACCOUNTS = ["HYPEX", "ShiinaBR", "FireMonkey"]

# Maximum number of items returned per account
MAX_ITEMS_PER_ACCOUNT = 10

# HTTP request timeout per nitter mirror
REQUEST_TIMEOUT = 20

logger = logging.getLogger(__name__)


def _build_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _strip_username_prefix(text: str, username: str) -> str:
    """Nitter prefixes tweet titles with 'R by @user:' or '@user:'.
    Strip the leading '@username: ' for cleaner titles.
    """
    if not text:
        return ""
    cleaned = text.strip()
    prefix = f"@{username}:"
    if cleaned.lower().startswith(prefix.lower()):
        cleaned = cleaned[len(prefix):].strip()
    return cleaned


def _rewrite_url_to_twitter(nitter_url: str, username: str) -> str:
    """Convert a nitter tweet URL like
    'https://nitter.net/HYPEX/status/12345#m' to
    'https://twitter.com/HYPEX/status/12345'.
    """
    if not nitter_url:
        return ""

    try:
        parsed = urlparse(nitter_url)
    except Exception:
        return nitter_url

    path = parsed.path or ""
    # Strip fragments like '#m' that nitter appends
    if not path:
        return nitter_url

    # If we cannot parse the path, return as-is with twitter domain
    return f"https://twitter.com{path}"


def _extract_image_url(entry: Any) -> str:
    """Extract first media/image URL from a feedparser entry."""
    # feedparser exposes <media:content> as media_content list of dicts
    media_content = getattr(entry, "media_content", None)
    if media_content:
        if isinstance(media_content, list):
            for media in media_content:
                if isinstance(media, dict):
                    url = media.get("url")
                    if url:
                        return str(url).strip()

    # <media:thumbnail>
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail:
        if isinstance(media_thumbnail, list) and media_thumbnail:
            first = media_thumbnail[0]
            if isinstance(first, dict):
                url = first.get("url")
                if url:
                    return str(url).strip()
        elif isinstance(media_thumbnail, dict):
            url = media_thumbnail.get("url")
            if url:
                return str(url).strip()

    # Fall back to parsing <img> tags out of the description/summary HTML
    summary = getattr(entry, "summary", "") or ""
    if summary and "<img" in summary:
        # Cheap extraction: find first src="..."
        try:
            start = summary.index('src="') + 5
            end = summary.index('"', start)
            return summary[start:end]
        except ValueError:
            pass

    return ""


def _parse_published(entry: Any) -> str:
    """Return ISO-8601 UTC string for the entry's published date."""
    published_parsed = getattr(entry, "published_parsed", None)
    if published_parsed:
        try:
            dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    return getattr(entry, "published", "") or ""


def _entry_text(entry: Any) -> str:
    """Return the tweet text from a feedparser entry, preferring title."""
    title = getattr(entry, "title", "") or ""
    if title:
        return title.strip()

    summary = getattr(entry, "summary", "") or ""
    if summary:
        # Strip HTML tags from summary as a fallback
        try:
            from bs4 import BeautifulSoup  # type: ignore
            return BeautifulSoup(summary, "lxml").get_text(" ", strip=True)
        except Exception:
            return summary.strip()

    return ""


async def _fetch_account(username: str) -> list[dict]:
    """Fetch latest posts for a single account, trying each nitter mirror.

    Returns up to MAX_ITEMS_PER_ACCOUNT items. Returns [] if all mirrors fail.
    """
    headers = {
        "User-Agent": _build_user_agent(),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    fetched_at = datetime.now(timezone.utc).isoformat()

    for instance in NITTER_INSTANCES:
        rss_url = f"{instance.rstrip('/')}/{username}/rss"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(rss_url, headers=headers)
                response.raise_for_status()
                content = response.content
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Nitter mirror %s returned HTTP %s for @%s",
                instance,
                e.response.status_code,
                username,
            )
            continue
        except httpx.RequestError as e:
            logger.warning(
                "Nitter mirror %s request failed for @%s: %s",
                instance,
                username,
                e,
            )
            continue
        except Exception as e:
            logger.warning(
                "Unexpected error fetching @%s from %s: %s",
                username,
                instance,
                e,
            )
            continue

        # Parse RSS in a thread (feedparser is sync/CPU-bound)
        try:
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, content)
        except Exception as e:
            logger.warning("Failed to parse RSS from %s for @%s: %s", instance, username, e)
            continue

        entries = feed.get("entries", []) if isinstance(feed, dict) or hasattr(feed, "get") else []
        if not entries:
            # Some mirrors return 200 with empty feed when rate-limited; try next
            logger.warning(
                "Nitter mirror %s returned no entries for @%s, trying next mirror",
                instance,
                username,
            )
            continue

        items: list[dict] = []
        for entry in entries[:MAX_ITEMS_PER_ACCOUNT]:
            text = _entry_text(entry)
            text = _strip_username_prefix(text, username)
            if not text:
                continue

            nitter_link = getattr(entry, "link", "") or ""
            url = _rewrite_url_to_twitter(nitter_link, username)
            if not url:
                continue

            title = text if len(text) <= 100 else text[:100].rstrip() + "..."
            image_url = _extract_image_url(entry)
            published_at = _parse_published(entry)

            items.append({
                "title": title,
                "url": url,
                "content": text,
                "image_url": image_url,
                "published_at": published_at,
                "is_leak": True,
                "_source": f"x.com/{username}",
                "_fetched_at": fetched_at,
            })

        if items:
            logger.info(
                "Fetched %d posts for @%s from %s",
                len(items),
                username,
                instance,
            )
            return items

    logger.warning("All nitter mirrors failed for @%s", username)
    return []


async def fetch_leak_sources() -> list[dict]:
    """Fetch latest tweets from leak accounts via Nitter RSS feeds.

    Tries multiple nitter mirrors per account. All returned items are marked
    with is_leak=True (source_level=3 is applied by the normalizer).

    Each item dict contains:
        title, url, content, image_url, published_at,
        is_leak, _source, _fetched_at
    """
    # Run all account fetches concurrently
    tasks = [_fetch_account(username) for username in LEAK_ACCOUNTS]
    per_account_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict] = []
    for username, account_result in zip(LEAK_ACCOUNTS, per_account_results):
        if isinstance(account_result, Exception):
            logger.warning("Fetch failed for @%s: %s", username, account_result)
            continue
        results.extend(account_result)

    logger.info("Fetched %d leak posts across %d accounts", len(results), len(LEAK_ACCOUNTS))
    return results
