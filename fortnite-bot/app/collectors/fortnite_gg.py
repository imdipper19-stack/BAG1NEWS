"""Collector for fortnite.gg unreleased / leaked cosmetics pages.

The site sits behind Cloudflare bot protection, so plain httpx gets 403.
We use Playwright (Chromium) with a few stealth tweaks to look like a
real browser. After the page renders, we extract cosmetic cards from the
DOM with the same parsing strategies as before.

Falls back gracefully to httpx + JSON-blob extraction if Playwright is
unavailable, which keeps the collector working in environments where
Chromium binaries are missing.
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LEAKS_URL = "https://fortnite.gg/leaks"
UNRELEASED_URL = "https://fortnite.gg/best-unreleased-cosmetics"
BASE_URL = "https://fortnite.gg"
MAX_ITEMS_PER_PAGE = 30
PAGE_LOAD_TIMEOUT_MS = 45_000
WAIT_AFTER_LOAD_MS = 2_500

_COSMETIC_HREF_RE = re.compile(r"/cosmetics\?(?:[^#]*&)?id=(\d+)", re.IGNORECASE)

_RARITY_KEYWORDS = (
    "common", "uncommon", "rare", "epic", "legendary", "mythic",
    "exotic", "transcendent", "marvel", "dc", "icon", "gaming",
    "starwars", "frozen", "lava", "dark", "shadow", "slurp", "gold",
)
_TYPE_KEYWORDS = (
    "outfit", "skin", "emote", "pickaxe", "backbling", "back bling",
    "glider", "wrap", "music", "loading", "contrail", "spray",
    "emoji", "banner", "bundle", "kicks", "sidekick", "jam track",
    "instrument", "guitar", "bass", "drum", "keytar", "mic",
    "aura", "lego", "build", "decor", "car", "decal", "wheel",
    "trail", "boost",
)


def _ua() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


# ---------------------------------------------------------------------------
# Playwright path (primary)
# ---------------------------------------------------------------------------


async def _fetch_html_playwright(url: str) -> str | None:
    """Render fortnite.gg with a real browser to get past Cloudflare."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        logger.warning("Playwright not installed — falling back to httpx")
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent=_ua(),
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

            # Mask navigator.webdriver to defeat the simplest bot checks
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )

            page = await context.new_page()
            try:
                await page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
                # Give Cloudflare's challenge JS a moment to clear
                await page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
                # Wait for at least one cosmetic link to show up
                try:
                    await page.wait_for_selector(
                        "a[href*='/cosmetics?id=']",
                        timeout=10_000,
                    )
                except Exception:
                    # No selector match — page might use a different layout,
                    # but we can still grab whatever HTML is there.
                    pass
                html = await page.content()
            finally:
                await context.close()
                await browser.close()
            return html
    except Exception as e:
        logger.warning("Playwright fetch failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# httpx path (fallback)
# ---------------------------------------------------------------------------


async def _fetch_html_httpx(url: str) -> str | None:
    headers = {
        "User-Agent": _ua(),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as e:
        logger.warning("fortnite.gg HTTP %s for %s", e.response.status_code, url)
        return None
    except httpx.RequestError as e:
        logger.warning("fortnite.gg request error for %s: %s", url, e)
        return None
    except Exception as e:
        logger.warning("fortnite.gg unexpected error for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_cosmetic_links(soup: BeautifulSoup, category: str, fetched_at: str) -> list[dict]:
    items: list[dict] = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        match = _COSMETIC_HREF_RE.search(href)
        if not match:
            continue
        item = _build_item_from_card(
            a_tag, href, category, fetched_at, cosmetic_id=match.group(1)
        )
        if item:
            items.append(item)
    return items


def _parse_class_item_cards(soup: BeautifulSoup, category: str, fetched_at: str) -> list[dict]:
    items: list[dict] = []
    selectors: tuple[dict, ...] = (
        {"name": "a", "class_": "item"},
        {"name": "a", "class_": lambda c: c and any(
            "item" in cls.lower() or "cosmetic" in cls.lower() for cls in c
        )},
        {"name": "div", "class_": lambda c: c and any(
            "item" in cls.lower() or "card" in cls.lower() for cls in c
        )},
    )
    for selector in selectors:
        found = soup.find_all(**selector)
        if not found:
            continue
        for tag in found:
            href = tag.get("href") or ""
            if not href:
                inner_a = tag.find("a", href=True)
                if inner_a:
                    href = inner_a["href"]
            if not href:
                continue
            item = _build_item_from_card(tag, href, category, fetched_at)
            if item:
                items.append(item)
        if items:
            break
    return items


def _build_item_from_card(
    tag: Any, href: str, category: str, fetched_at: str, cosmetic_id: str | None = None
) -> dict | None:
    url = _absolute_url(href)
    if not url:
        return None
    title = _extract_title(tag)
    if not title:
        return None
    image_url = _extract_image_url(tag)
    rarity = _extract_rarity(tag)
    cosmetic_type = _extract_type(tag)
    content = _build_content(rarity, cosmetic_type)

    item = {
        "title": title,
        "url": url,
        "content": content,
        "image_url": image_url,
        "category": category,
        "published_at": "",
        "is_leak": True,
        "_source": f"fortnite.gg/{category}",
        "_fetched_at": fetched_at,
    }
    if cosmetic_id:
        item["cosmetic_id"] = cosmetic_id
    return item


def _extract_title(tag: Any) -> str:
    img = tag.find("img") if hasattr(tag, "find") else None
    if img is not None:
        alt = (img.get("alt") or "").strip()
        if alt:
            return _clean_title(alt)
    for attr in ("title", "aria-label", "data-name", "data-title"):
        value = tag.get(attr) if hasattr(tag, "get") else None
        if value:
            cleaned = _clean_title(str(value))
            if cleaned:
                return cleaned
    if hasattr(tag, "find"):
        heading = tag.find(["h1", "h2", "h3", "h4", "h5", "span"])
        if heading is not None:
            cleaned = _clean_title(heading.get_text(" ", strip=True))
            if cleaned:
                return cleaned
    if hasattr(tag, "get_text"):
        cleaned = _clean_title(tag.get_text(" ", strip=True))
        if cleaned:
            return cleaned
    return ""


def _clean_title(raw: str) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw).strip()
    if cleaned.lower().startswith("fortnite "):
        cleaned = cleaned[len("fortnite "):].strip()
    return cleaned


def _extract_image_url(tag: Any) -> str:
    if not hasattr(tag, "find"):
        return ""
    img = tag.find("img")
    if img is None:
        return ""
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        value = img.get(attr)
        if value:
            return _absolute_url(str(value).strip())
    srcset = img.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first:
            return _absolute_url(first)
    return ""


def _extract_rarity(tag: Any) -> str:
    classes = _all_class_strings(tag)
    for cls in classes:
        lowered = cls.lower()
        for rarity in _RARITY_KEYWORDS:
            if rarity == lowered or lowered.endswith(f"-{rarity}") or lowered.endswith(f"_{rarity}"):
                return rarity.capitalize()
    for attr in ("data-rarity", "data-color", "data-series"):
        value = tag.get(attr) if hasattr(tag, "get") else None
        if value:
            return str(value).strip().capitalize()
    return ""


def _extract_type(tag: Any) -> str:
    classes = _all_class_strings(tag)
    for cls in classes:
        lowered = cls.lower()
        for type_kw in _TYPE_KEYWORDS:
            normalized = type_kw.replace(" ", "")
            if normalized in lowered:
                return type_kw.title()
    for attr in ("data-type", "data-category"):
        value = tag.get(attr) if hasattr(tag, "get") else None
        if value:
            return str(value).strip().title()
    return ""


def _all_class_strings(tag: Any) -> Iterable[str]:
    if not hasattr(tag, "get"):
        return []
    classes: list[str] = []
    own = tag.get("class")
    if own:
        classes.extend(own if isinstance(own, list) else [str(own)])
    if hasattr(tag, "find_all"):
        for child in tag.find_all(True, class_=True):
            child_classes = child.get("class")
            if child_classes:
                classes.extend(
                    child_classes if isinstance(child_classes, list)
                    else [str(child_classes)]
                )
    return classes


def _build_content(rarity: str, cosmetic_type: str) -> str:
    parts = [p for p in (rarity, cosmetic_type) if p]
    return " · ".join(parts)


def _absolute_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("http://", "https://", "//")):
        return f"https:{href}" if href.startswith("//") else href
    return f"{BASE_URL}{href}" if href.startswith("/") else f"{BASE_URL}/{href}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _fetch_page(url: str, category: str) -> list[dict]:
    fetched_at = datetime.now(timezone.utc).isoformat()

    # Try Playwright first, fall back to httpx
    html = await _fetch_html_playwright(url)
    if html is None:
        logger.info("Playwright unavailable / failed, trying httpx for %s", url)
        html = await _fetch_html_httpx(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.warning("fortnite.gg HTML parse failed for %s: %s", url, e)
        return []

    items = _parse_cosmetic_links(soup, category, fetched_at)
    if not items:
        items = _parse_class_item_cards(soup, category, fetched_at)

    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= MAX_ITEMS_PER_PAGE:
            break

    logger.info(
        "fortnite.gg %s: parsed %d items (raw=%d) from %s",
        category, len(unique), len(items), url,
    )
    return unique


async def fetch_fortnite_gg() -> list[dict]:
    """Scrape both fortnite.gg leak pages concurrently."""
    leaks_task = _fetch_page(LEAKS_URL, category="leaks")
    unreleased_task = _fetch_page(UNRELEASED_URL, category="unreleased")
    results = await asyncio.gather(leaks_task, unreleased_task, return_exceptions=True)

    items: list[dict] = []
    for category, result in zip(("leaks", "unreleased"), results):
        if isinstance(result, Exception):
            logger.warning("fortnite.gg %s page failed: %s", category, result)
            continue
        items.extend(result)

    logger.info("fortnite.gg: collected %d items total", len(items))
    return items
