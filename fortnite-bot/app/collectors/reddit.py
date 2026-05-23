"""Auth-less Reddit collector for r/FortniteLeaks.

Reddit aggressively blocks server-side IPs that hit reddit.com directly
with default User-Agents. We try several strategies in order:

  1. ``old.reddit.com/r/.../new.json`` with a rotating browser User-Agent
     (the old domain has a much more permissive bot policy).
  2. ``www.reddit.com/r/.../.rss`` — Atom feed parsed with feedparser.
  3. ``redlib.catsarch.com/r/.../new.json`` — community-run proxy that
     mirrors Reddit content.

We pick the first source that returns ≥1 valid post. All retries use
exponential backoff. Standard skip rules apply (NSFW, stickied, score < 5).
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Quality / safety thresholds
MIN_SCORE = 5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# Several browser User-Agents we rotate through. Reddit's WAF heuristics
# look for both the UA and the Accept-Language combination, so we vary
# both.
_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
    "Gecko/20100101 Firefox/124.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

_ACCEPT_LANGS = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-US,en;q=0.5"]

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_INVALID_THUMBNAIL_VALUES = {"", "self", "default", "nsfw", "spoiler", "image"}


def _build_headers() -> dict[str, str]:
    """Return browser-like headers with rotated UA / Accept-Language."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/json, text/html, */*",
        "Accept-Language": random.choice(_ACCEPT_LANGS),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def _looks_like_image_url(url: str) -> bool:
    if not url:
        return False
    lower = url.lower().split("?", 1)[0]
    if lower.endswith(_IMAGE_EXTENSIONS):
        return True
    return "i.redd.it" in lower or "i.reddituploads.com" in lower


def _extract_image_url_from_post(post: dict) -> str:
    """Pick the best image URL from a parsed Reddit JSON post."""
    preview = post.get("preview")
    if isinstance(preview, dict):
        images = preview.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                source = first.get("source")
                if isinstance(source, dict):
                    src_url = source.get("url")
                    if src_url:
                        return str(src_url).replace("&amp;", "&").strip()

    post_url = post.get("url") or post.get("url_overridden_by_dest") or ""
    if isinstance(post_url, str) and _looks_like_image_url(post_url):
        return post_url.strip()

    thumbnail = post.get("thumbnail") or ""
    if (
        isinstance(thumbnail, str)
        and thumbnail not in _INVALID_THUMBNAIL_VALUES
        and thumbnail.startswith("http")
    ):
        return thumbnail.strip()

    return ""


def _convert_created_utc(created_utc: Any) -> str:
    if created_utc is None:
        return ""
    try:
        ts = float(created_utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


async def _fetch_with_retry(
    url: str,
    headers: dict | None = None,
    params: dict | None = None,
) -> httpx.Response | None:
    """Fetch URL with exponential backoff. Returns None on terminal failure."""
    backoff = 1.5
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                response = await client.get(
                    url, headers=headers or _build_headers(), params=params
                )
                if response.status_code in (429, 503):
                    logger.warning(
                        "Reddit fetch %s got %s (attempt %d/%d), backing off",
                        url,
                        response.status_code,
                        attempt,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(backoff ** attempt + random.uniform(0, 1))
                    continue
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as e:
            logger.warning(
                "HTTP %s for %s (attempt %d/%d)",
                e.response.status_code,
                url,
                attempt,
                MAX_RETRIES,
            )
            if e.response.status_code == 403:
                # 403 doesn't get better with retries, give up immediately
                return None
            await asyncio.sleep(backoff ** attempt)
        except httpx.RequestError as e:
            logger.warning("Request error %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, e)
            await asyncio.sleep(backoff ** attempt)
        except Exception as e:
            logger.warning("Unexpected error %s: %s", url, e)
            return None
    return None


def _filter_post(post: dict, fetched_at: str) -> dict | None:
    """Apply standard skip rules and convert to unified item dict."""
    if not isinstance(post, dict):
        return None
    if post.get("stickied") or post.get("pinned") or post.get("over_18"):
        return None

    try:
        score = int(post.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    if score < MIN_SCORE:
        return None

    title = (post.get("title") or "").strip()
    if not title:
        return None

    permalink = post.get("permalink") or ""
    url = f"https://reddit.com{permalink}" if permalink else (post.get("url") or "")
    if not url:
        return None

    return {
        "title": title,
        "url": url,
        "content": (post.get("selftext") or "").strip(),
        "image_url": _extract_image_url_from_post(post),
        "score": score,
        "published_at": _convert_created_utc(post.get("created_utc")),
        "is_leak": True,
        "_source": "reddit/FortniteLeaks",
        "_fetched_at": fetched_at,
    }


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


async def _fetch_via_old_json(limit: int, fetched_at: str) -> list[dict]:
    url = "https://old.reddit.com/r/FortniteLeaks/new.json"
    response = await _fetch_with_retry(url, params={"limit": limit, "raw_json": 1})
    if response is None:
        return []
    try:
        data = response.json()
    except ValueError:
        return []

    children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
    if not isinstance(children, list):
        return []

    items: list[dict] = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if not post:
            continue
        item = _filter_post(post, fetched_at)
        if item:
            items.append(item)
        if len(items) >= limit:
            break
    return items


async def _fetch_via_www_json(limit: int, fetched_at: str) -> list[dict]:
    url = "https://www.reddit.com/r/FortniteLeaks/new.json"
    response = await _fetch_with_retry(url, params={"limit": limit, "raw_json": 1})
    if response is None:
        return []
    try:
        data = response.json()
    except ValueError:
        return []

    children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
    if not isinstance(children, list):
        return []

    items: list[dict] = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if not post:
            continue
        item = _filter_post(post, fetched_at)
        if item:
            items.append(item)
        if len(items) >= limit:
            break
    return items


_RSS_IMG_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)


async def _fetch_via_rss(limit: int, fetched_at: str) -> list[dict]:
    """RSS feed (works when JSON is blocked)."""
    url = "https://www.reddit.com/r/FortniteLeaks/.rss"
    response = await _fetch_with_retry(url)
    if response is None:
        return []

    try:
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(None, feedparser.parse, response.content)
    except Exception as e:
        logger.warning("Failed to parse Reddit RSS: %s", e)
        return []

    if feed.get("bozo") and not feed.get("entries"):
        return []

    items: list[dict] = []
    for entry in feed.get("entries", [])[: limit * 2]:
        title = (getattr(entry, "title", "") or "").strip()
        link = getattr(entry, "link", "") or ""
        if not title or not link:
            continue

        # RSS doesn't carry score/over_18/stickied, so we can't apply most
        # filters; we only require a link and title. Score is set to 0 by
        # default — the scoring engine will still rate the post by
        # keywords + freshness.
        summary = getattr(entry, "summary", "") or ""
        image_url = ""
        if "<img" in summary:
            m = _RSS_IMG_RE.search(summary)
            if m:
                image_url = unescape(m.group(1))

        published_parsed = getattr(entry, "published_parsed", None)
        published_at = ""
        if published_parsed:
            try:
                published_at = datetime(
                    *published_parsed[:6], tzinfo=timezone.utc
                ).isoformat()
            except Exception:
                pass

        items.append({
            "title": title,
            "url": link,
            "content": "",
            "image_url": image_url,
            "score": 0,
            "published_at": published_at,
            "is_leak": True,
            "_source": "reddit/FortniteLeaks",
            "_fetched_at": fetched_at,
        })
        if len(items) >= limit:
            break
    return items


async def _fetch_via_redlib(limit: int, fetched_at: str) -> list[dict]:
    """Community-run Reddit proxy (libreddit / redlib)."""
    # A handful of public instances. We try them in order.
    instances = [
        "https://redlib.catsarch.com",
        "https://safereddit.com",
        "https://libreddit.privacydev.net",
    ]
    for instance in instances:
        url = f"{instance.rstrip('/')}/r/FortniteLeaks/new.json"
        response = await _fetch_with_retry(url, params={"limit": limit, "raw_json": 1})
        if response is None:
            continue
        try:
            data = response.json()
        except ValueError:
            continue

        children = (
            data.get("data", {}).get("children", []) if isinstance(data, dict) else []
        )
        if not isinstance(children, list) or not children:
            continue

        items: list[dict] = []
        for child in children:
            post = child.get("data") if isinstance(child, dict) else None
            if not post:
                continue
            item = _filter_post(post, fetched_at)
            if item:
                items.append(item)
            if len(items) >= limit:
                break
        if items:
            logger.info("Reddit collected via %s (%d items)", instance, len(items))
            return items
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_reddit_leaks(limit: int = 25) -> list[dict]:
    """Try multiple Reddit endpoints, return items from the first that works."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    strategies = (
        ("old.reddit.com/json", _fetch_via_old_json),
        ("www.reddit.com/json", _fetch_via_www_json),
        ("www.reddit.com/.rss", _fetch_via_rss),
        ("redlib", _fetch_via_redlib),
    )

    for name, fn in strategies:
        try:
            items = await fn(limit, fetched_at)
        except Exception as e:
            logger.warning("Reddit strategy %s threw: %s", name, e)
            items = []
        if items:
            logger.info("Reddit fetched %d items via %s", len(items), name)
            return items

    logger.warning("All Reddit strategies failed")
    return []
