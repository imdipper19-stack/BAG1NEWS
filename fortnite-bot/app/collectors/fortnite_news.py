import httpx
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from typing import Any

NEWS_URL = "https://www.fortnite.com/news"
CMS_API_URL = "https://www.epicgames.com/fortnite/en-US/news"
logger = logging.getLogger(__name__)


async def fetch_official_news() -> list[dict]:
    """Fetch official Fortnite news articles from fortnite.com/news.

    Tries the JSON API first, then falls back to HTML scraping.
    Returns a list of dicts with: title, url, content, image_url,
    published_at, _source, _fetched_at.
    """
    articles = await _fetch_via_json_api()
    if articles:
        return articles

    logger.info("JSON API returned no results, falling back to HTML scraping")
    articles = await _fetch_via_html(NEWS_URL)
    if articles:
        return articles

    # Last-resort: try the Epic Games URL as HTML too
    articles = await _fetch_via_html(CMS_API_URL)
    return articles


async def _fetch_via_json_api() -> list[dict]:
    """Try to get news via JSON API (Accept: application/json header)."""
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    fetched_at = datetime.now(timezone.utc).isoformat()

    for url in (NEWS_URL, CMS_API_URL):
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "application/json" not in content_type:
                    logger.debug("URL %s returned non-JSON content-type: %s", url, content_type)
                    continue
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "HTTP error fetching JSON from %s: %s %s",
                url,
                e.response.status_code,
                e.response.text[:200],
            )
            continue
        except httpx.RequestError as e:
            logger.warning("Request error fetching JSON from %s: %s", url, e)
            continue
        except Exception as e:
            logger.warning("Unexpected error fetching JSON from %s: %s", url, e)
            continue

        articles = _parse_json_response(data, fetched_at)
        if articles:
            logger.info("Fetched %d articles via JSON API from %s", len(articles), url)
            return articles

    return []


def _parse_json_response(data: Any, fetched_at: str) -> list[dict]:
    """Parse various JSON response shapes that Epic/Fortnite APIs may return."""
    result: list[dict] = []

    # Shape 1: {"data": {"articles": [...]}}  or {"articles": [...]}
    if isinstance(data, dict):
        articles_raw = (
            data.get("articles")
            or data.get("data", {}).get("articles")
            or data.get("items")
            or data.get("data", {}).get("items")
            or []
        )
        if isinstance(articles_raw, list):
            for article in articles_raw:
                parsed = _parse_json_article(article, fetched_at)
                if parsed:
                    result.append(parsed)

    # Shape 2: top-level list
    elif isinstance(data, list):
        for article in data:
            parsed = _parse_json_article(article, fetched_at)
            if parsed:
                result.append(parsed)

    return result


def _parse_json_article(article: Any, fetched_at: str) -> dict | None:
    """Convert a single JSON article object to the unified dict format."""
    if not isinstance(article, dict):
        return None

    title = (
        article.get("title")
        or article.get("headline")
        or article.get("name")
        or ""
    )
    if not title:
        return None

    # URL — may be relative or absolute
    url = (
        article.get("url")
        or article.get("link")
        or article.get("slug")
        or ""
    )
    if url and not url.startswith("http"):
        url = f"https://www.fortnite.com{url if url.startswith('/') else '/' + url}"

    # Content / description
    content = (
        article.get("content")
        or article.get("description")
        or article.get("body")
        or article.get("summary")
        or ""
    )

    # Image URL
    image_url = (
        article.get("image")
        or article.get("imageUrl")
        or article.get("image_url")
        or article.get("thumbnail")
        or ""
    )
    # Sometimes image is a nested dict
    if isinstance(image_url, dict):
        image_url = (
            image_url.get("src")
            or image_url.get("url")
            or image_url.get("href")
            or ""
        )

    # Published date
    published_at = (
        article.get("publishedAt")
        or article.get("published_at")
        or article.get("date")
        or article.get("createdAt")
        or ""
    )

    return {
        "title": str(title).strip(),
        "url": str(url).strip(),
        "content": str(content).strip(),
        "image_url": str(image_url).strip(),
        "published_at": str(published_at).strip() if published_at else "",
        "_source": "fortnite.com/news",
        "_fetched_at": fetched_at,
    }


async def _fetch_via_html(url: str) -> list[dict]:
    """Parse an HTML page with BeautifulSoup4 looking for article/news cards."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error fetching HTML from %s: %s %s",
            url,
            e.response.status_code,
            e.response.text[:200],
        )
        return []
    except httpx.RequestError as e:
        logger.error("Request error fetching HTML from %s: %s", url, e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching HTML from %s: %s", url, e)
        return []

    soup = BeautifulSoup(html, "lxml")
    articles: list[dict] = []

    # Strategy 1: look for <article> tags
    for tag in soup.find_all("article"):
        item = _extract_from_tag(tag, url, fetched_at)
        if item:
            articles.append(item)

    # Strategy 2: common CSS class patterns used by Epic/Fortnite sites
    if not articles:
        card_selectors = [
            {"class": lambda c: c and any(
                kw in " ".join(c).lower()
                for kw in ("news-card", "newscard", "article-card", "blog-card",
                           "news-item", "newsitem", "post-card", "postcard")
            )},
        ]
        for selector in card_selectors:
            for tag in soup.find_all(True, selector):
                item = _extract_from_tag(tag, url, fetched_at)
                if item:
                    articles.append(item)
            if articles:
                break

    # Strategy 3: look for <a> tags that contain both an image and a heading
    if not articles:
        for a_tag in soup.find_all("a", href=True):
            heading = a_tag.find(["h1", "h2", "h3", "h4"])
            img = a_tag.find("img")
            if heading and img:
                title = heading.get_text(strip=True)
                if not title:
                    continue
                href = a_tag["href"]
                if not href.startswith("http"):
                    href = f"https://www.fortnite.com{href if href.startswith('/') else '/' + href}"
                image_url = img.get("src") or img.get("data-src") or ""
                articles.append({
                    "title": title,
                    "url": href,
                    "content": "",
                    "image_url": image_url,
                    "published_at": "",
                    "_source": "fortnite.com/news",
                    "_fetched_at": fetched_at,
                })

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in articles:
        key = item["url"] or item["title"]
        if key and key not in seen_urls:
            seen_urls.add(key)
            unique.append(item)

    logger.info("Fetched %d articles via HTML scraping from %s", len(unique), url)
    return unique


def _extract_from_tag(tag: Any, base_url: str, fetched_at: str) -> dict | None:
    """Extract article fields from a BeautifulSoup tag."""
    # Title: prefer heading tags, fall back to any text
    heading = tag.find(["h1", "h2", "h3", "h4"])
    title = heading.get_text(strip=True) if heading else ""
    if not title:
        # Try aria-label or title attribute on the tag itself
        title = tag.get("aria-label") or tag.get("title") or ""
    if not title:
        return None

    # URL
    a_tag = tag.find("a", href=True) or (tag if tag.name == "a" and tag.get("href") else None)
    href = a_tag["href"] if a_tag else ""
    if href and not href.startswith("http"):
        href = f"https://www.fortnite.com{href if href.startswith('/') else '/' + href}"

    # Image
    img = tag.find("img")
    image_url = ""
    if img:
        image_url = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""

    # Description / content
    desc_tag = tag.find(["p", "span"], class_=lambda c: c and any(
        kw in " ".join(c).lower() for kw in ("desc", "summary", "excerpt", "body", "content")
    ))
    if not desc_tag:
        # Try any <p> that isn't the title
        for p in tag.find_all("p"):
            text = p.get_text(strip=True)
            if text and text != title:
                desc_tag = p
                break
    content = desc_tag.get_text(strip=True) if desc_tag else ""

    # Published date
    time_tag = tag.find("time")
    published_at = ""
    if time_tag:
        published_at = time_tag.get("datetime") or time_tag.get_text(strip=True) or ""

    return {
        "title": title,
        "url": href,
        "content": content,
        "image_url": image_url,
        "published_at": published_at,
        "_source": "fortnite.com/news",
        "_fetched_at": fetched_at,
    }
