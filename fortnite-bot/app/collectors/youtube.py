import asyncio
import feedparser
import httpx
import logging
from datetime import datetime, timezone
from typing import Any

# Official Fortnite YouTube channel ID
FORTNITE_CHANNEL_ID = "UCkgU7AlFGnxFMi4JrGHoFoA"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={FORTNITE_CHANNEL_ID}"

# Limit how many of the most-recent videos we return
MAX_VIDEOS = 15

logger = logging.getLogger(__name__)


def _extract_video_id(entry: Any) -> str:
    """Extract video ID from a feedparser entry.

    Tries the yt_videoid field first (parsed from <yt:videoId>), then falls
    back to parsing the entry URL.
    """
    video_id = getattr(entry, "yt_videoid", None)
    if video_id:
        return str(video_id).strip()

    link = getattr(entry, "link", "") or ""
    if "v=" in link:
        return link.split("v=")[-1].split("&")[0].strip()

    return ""


def _extract_thumbnail(entry: Any, video_id: str) -> str:
    """Return the best available thumbnail URL for the entry.

    Preference: <media:thumbnail url="..."/> from the feed, then a derived
    maxresdefault URL based on the video id.
    """
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail:
        # feedparser exposes this as a list of dicts: [{"url": "...", ...}]
        if isinstance(media_thumbnail, list) and media_thumbnail:
            url = media_thumbnail[0].get("url") if isinstance(media_thumbnail[0], dict) else None
            if url:
                return str(url).strip()
        elif isinstance(media_thumbnail, dict):
            url = media_thumbnail.get("url")
            if url:
                return str(url).strip()

    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

    return ""


def _parse_published(entry: Any) -> str:
    """Return an ISO-8601 UTC string for the entry's published date, or empty string."""
    # feedparser provides published_parsed as a time.struct_time in UTC
    published_parsed = getattr(entry, "published_parsed", None)
    if published_parsed:
        try:
            dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    # Fall back to the raw string
    return getattr(entry, "published", "") or ""


async def fetch_youtube_videos() -> list[dict]:
    """Fetch latest videos from official Fortnite YouTube channel via RSS.

    Uses httpx.AsyncClient to download the RSS XML, then parses it with
    feedparser. Returns at most MAX_VIDEOS most recent videos.

    Each entry in the returned list has:
        title, url, content, image_url, published_at,
        video_id, _source, _fetched_at
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/atom+xml, application/xml, text/xml",
    }

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(RSS_URL, headers=headers)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error fetching YouTube RSS: %s %s",
            e.response.status_code,
            e.response.text[:200],
        )
        return []
    except httpx.RequestError as e:
        logger.error("Request error fetching YouTube RSS: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching YouTube RSS: %s", e)
        return []

    # feedparser.parse is synchronous and CPU-bound; run it in a thread pool
    try:
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(None, feedparser.parse, content)
    except Exception as e:
        logger.error("Failed to parse YouTube RSS XML: %s", e)
        return []

    if feed.get("bozo") and not feed.get("entries"):
        exc = feed.get("bozo_exception")
        logger.error("Malformed YouTube RSS feed: %s", exc)
        return []

    fetched_at = datetime.now(timezone.utc).isoformat()
    result: list[dict] = []

    for entry in feed.get("entries", []):
        video_id = _extract_video_id(entry)
        if not video_id:
            logger.warning(
                "Could not extract video_id from YouTube RSS entry: %s",
                getattr(entry, "link", ""),
            )
            continue

        title = (getattr(entry, "title", "") or "").strip()
        url = f"https://www.youtube.com/watch?v={video_id}"

        # Description / summary — feedparser exposes <media:description> as
        # media_description, and the standard <summary>/<content> elements as
        # summary / content.
        content_text = ""
        media_description = getattr(entry, "media_description", None)
        if media_description:
            content_text = str(media_description).strip()
        if not content_text:
            summary = getattr(entry, "summary", None)
            if summary:
                content_text = str(summary).strip()

        image_url = _extract_thumbnail(entry, video_id)
        published_at = _parse_published(entry)

        result.append({
            "title": title,
            "url": url,
            "content": content_text,
            "image_url": image_url,
            "published_at": published_at,
            "video_id": video_id,
            "_source": "youtube/fortnite",
            "_fetched_at": fetched_at,
        })

        if len(result) >= MAX_VIDEOS:
            break

    logger.info("Fetched %d videos from YouTube RSS", len(result))
    return result
