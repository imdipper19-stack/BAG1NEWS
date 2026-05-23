import httpx
import logging
from datetime import datetime, timezone
from typing import Any

BASE_URL = "https://fortnite-api.com"
logger = logging.getLogger(__name__)


async def fetch_shop() -> list[dict]:
    """Fetch current item shop from fortnite-api.com/v2/shop"""
    url = f"{BASE_URL}/v2/shop"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error fetching shop: %s %s", e.response.status_code, e.response.text)
        return []
    except httpx.RequestError as e:
        logger.error("Request error fetching shop: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching shop: %s", e)
        return []

    fetched_at = datetime.now(timezone.utc).isoformat()
    entries = data.get("data", {}).get("entries", [])
    if not isinstance(entries, list):
        logger.warning("Unexpected shop data format: 'entries' is not a list")
        return []

    result = []
    for entry in entries:
        item: dict[str, Any] = {}

        # Prefer brItems list, fall back to top-level fields
        br_items = entry.get("brItems") or []
        if br_items:
            first = br_items[0]
            item["name"] = first.get("name", "")
            item["description"] = first.get("description", "")
            item["images"] = first.get("images", {})
            item["rarity"] = first.get("rarity", {}).get("value", "")
        else:
            item["name"] = entry.get("bundle", {}).get("name", "") if entry.get("bundle") else ""
            item["description"] = entry.get("bundle", {}).get("info", "") if entry.get("bundle") else ""
            item["images"] = {"icon": entry.get("bundle", {}).get("image", "")} if entry.get("bundle") else {}
            item["rarity"] = ""

        item["price"] = entry.get("finalPrice", entry.get("regularPrice", 0))
        item["_source"] = "fortnite-api/shop"
        item["_fetched_at"] = fetched_at
        result.append(item)

    return result


async def fetch_news() -> list[dict]:
    """Fetch in-game news from fortnite-api.com/v2/news"""
    url = f"{BASE_URL}/v2/news"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error fetching news: %s %s", e.response.status_code, e.response.text)
        return []
    except httpx.RequestError as e:
        logger.error("Request error fetching news: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching news: %s", e)
        return []

    fetched_at = datetime.now(timezone.utc).isoformat()
    news_data = data.get("data", {})
    result = []

    # The API returns separate sections: br, stw, creative
    for section_key in ("br", "stw", "creative"):
        section = news_data.get(section_key)
        if not section:
            continue
        motds = section.get("motds", [])
        if not isinstance(motds, list):
            continue
        for motd in motds:
            item: dict[str, Any] = {
                "title": motd.get("title", ""),
                "body": motd.get("body", ""),
                "image": motd.get("image", ""),
                "section": section_key,
                "_source": "fortnite-api/news",
                "_fetched_at": fetched_at,
            }
            result.append(item)

    return result


async def fetch_new_cosmetics() -> list[dict]:
    """Fetch newly added cosmetics from fortnite-api.com/v2/cosmetics/new"""
    url = f"{BASE_URL}/v2/cosmetics/new"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error fetching new cosmetics: %s %s", e.response.status_code, e.response.text)
        return []
    except httpx.RequestError as e:
        logger.error("Request error fetching new cosmetics: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error fetching new cosmetics: %s", e)
        return []

    fetched_at = datetime.now(timezone.utc).isoformat()
    cosmetics_data = data.get("data", {})
    result = []

    # Items are grouped by type: br, tracks, instruments, cars, lego, legoKits, beans
    for type_key, items in cosmetics_data.items():
        if not isinstance(items, list):
            continue
        for cosmetic in items:
            item: dict[str, Any] = {
                "name": cosmetic.get("name", ""),
                "description": cosmetic.get("description", ""),
                "rarity": cosmetic.get("rarity", {}).get("value", "") if isinstance(cosmetic.get("rarity"), dict) else "",
                "images": cosmetic.get("images", {}),
                "type": cosmetic.get("type", {}).get("value", type_key) if isinstance(cosmetic.get("type"), dict) else type_key,
                "_source": "fortnite-api/cosmetics/new",
                "_fetched_at": fetched_at,
            }
            result.append(item)

    return result
