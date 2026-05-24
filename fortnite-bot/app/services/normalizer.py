"""Normalizer service.

Converts each collector's raw dict output into a unified ``RawItem`` schema.

Source-level rules from the spec:
    Level 1 (official): fortnite.com/news, official YouTube
    Level 2 (API/database): fortnite-api.com, fortnite.gg
    Level 3 (leak/datamine): X (HYPEX/ShiinaBR/FireMonkey), Reddit r/FortniteLeaks
    Level 4 (trend): not implemented yet

This step also strips leaker attribution from titles/content (we
position the channel as a primary insider) and filters out
already-released collabs that are no longer relevant news.
"""

from datetime import datetime
from typing import Optional
import logging

from app.schemas import RawItem
from app.services.title_cleaner import clean_content, clean_title

logger = logging.getLogger(__name__)


# Past collaborations / events that already happened — bot should not
# repackage them as "future news". Lowercase substrings, matched in
# title+content. Keep ordered by recency (newest first) so it's easy
# to extend.
_PAST_COLLABS = (
    "overwatch",            # 2024 collab — already happened
    "destiny 2",            # 2024
    "doctor who",           # 2024
    "doctor strange",       # 2024
    "wednesday",            # 2024
    "fall guys",            # multiple, already done
    "rocket league",        # crossover already done
    "metallica",            # 2024 jam track collab released
    "lady gaga festival",   # released
    "billie eilish festival",  # released
    "snoop dogg",           # released
    "tmnt",                 # already done
    "teenage mutant ninja turtles",
    "x-men 97",             # released
    "avatar",               # already done
    "the last airbender",   # already done
    "pirates of the caribbean",  # released
    "dragon ball",          # most arcs released
    "naruto",               # most arcs released
    "rick and morty",       # released
    "halo",                 # released
    "predator",             # released
    "alien xenomorph",      # released
    # 2024-2025 batch
    "crimson desert",       # collab released
    "family guy",           # released as part of Disney/Fox jam
    "lois griffin",         # Family Guy character
    "peter griffin",        # Family Guy character
    "stewie griffin",       # Family Guy character
    "bob's burgers",        # released
    "linda belcher",        # Bob's Burgers
    "king of the hill",     # released
    "peggy hill",           # King of the Hill
    "hank hill",            # King of the Hill
    "jason voorhees",       # Friday the 13th Fortnitemares — annual rerun
    "friday the 13th",      # annual Halloween rerun
    "fortnitemares",        # annual event — not "future" news
    # Rocket League / Rocket Racing legacy cars (collected as "skins")
    "fennec",               # RL car — already in game
    "dominus",              # RL car — already in game
    "the sentinel",         # RL car — already in game
    "octane",               # RL car — already in game
    # Other legacy
    "shatter spawn",        # passed seasonal mini-event (May 2024)
    "golden scythe",        # earned via Shatter Spawn — past
    "golden katana",        # earned via Shatter Spawn — past
    "golden",               # broad — covers any "Golden X" reward post
    "indigo kuno",          # released cosmetic, not future news
    "pie patron",            # released
)


def _is_past_collab(title: str, content: str) -> bool:
    """Return True if the item references an already-released collab."""
    text = f"{title or ''} {content or ''}".lower()
    return any(needle in text for needle in _PAST_COLLABS)


def _parse_dt(value: str | None) -> Optional[datetime]:
    """Parse ISO datetime string, return None on failure."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def normalize_fortnite_api(item: dict) -> RawItem | None:
    source = item.get("_source", "fortnite-api")
    name = clean_title(item.get("name") or item.get("title") or "")
    if not name:
        return None

    if "shop" in source:
        category = "item_shop"
    elif "news" in source:
        category = "official_news"
    elif "cosmetics" in source:
        category = "skin_leak"
    else:
        category = "general"

    item_id = item.get("id", "")
    url = item.get("url") or f"https://fortnite-api.com/items/{name.replace(' ', '-')}"

    image_url = ""
    images = item.get("images") or item.get("image", "")
    if isinstance(images, dict):
        image_url = images.get("icon") or images.get("featured") or ""
    elif isinstance(images, str):
        image_url = images

    content = clean_content(item.get("description") or item.get("body") or "")
    if _is_past_collab(name, content):
        return None

    return RawItem(
        source=source,
        source_level=2,
        title=name,
        url=url,
        content=content,
        image_url=image_url,
        category=category,
        published_at=_parse_dt(item.get("_fetched_at")),
        is_official=False,
        is_leak=False,
    )


def normalize_fortnite_news(item: dict) -> RawItem | None:
    title = clean_title(item.get("title", ""))
    url = item.get("url", "")
    if not title or not url:
        return None
    content = clean_content(item.get("content", ""))
    if _is_past_collab(title, content):
        return None
    return RawItem(
        source=item.get("_source", "fortnite.com/news"),
        source_level=1,
        title=title,
        url=url,
        content=content,
        image_url=item.get("image_url", ""),
        category="official_news",
        published_at=_parse_dt(item.get("published_at")) or _parse_dt(item.get("_fetched_at")),
        is_official=True,
        is_leak=False,
    )


def normalize_youtube(item: dict) -> RawItem | None:
    title = clean_title(item.get("title", ""))
    url = item.get("url", "")
    if not title or not url:
        return None
    content = clean_content(item.get("content", ""))
    if _is_past_collab(title, content):
        return None
    return RawItem(
        source=item.get("_source", "youtube/fortnite"),
        source_level=1,
        title=title,
        url=url,
        content=content,
        image_url=item.get("image_url", ""),
        category="official_news",
        published_at=_parse_dt(item.get("published_at")),
        is_official=True,
        is_leak=False,
    )


def normalize_leak_x(item: dict) -> RawItem | None:
    title = clean_title(item.get("title", ""))
    url = item.get("url", "")
    if not title or not url:
        return None
    content = clean_content(item.get("content", ""))
    if _is_past_collab(title, content):
        return None
    return RawItem(
        source=item.get("_source", "x.com"),
        source_level=3,
        title=title,
        url=url,
        content=content,
        image_url=item.get("image_url", ""),
        category="skin_leak",
        published_at=_parse_dt(item.get("published_at")),
        is_official=False,
        is_leak=True,
    )


def normalize_reddit(item: dict) -> RawItem | None:
    title = clean_title(item.get("title", ""))
    url = item.get("url", "")
    if not title or not url:
        return None
    content = clean_content(item.get("content", ""))
    if _is_past_collab(title, content):
        return None
    return RawItem(
        source=item.get("_source", "reddit/FortniteLeaks"),
        source_level=3,
        title=title,
        url=url,
        content=content,
        image_url=item.get("image_url", ""),
        category="leak_discussion",
        published_at=_parse_dt(item.get("published_at")),
        is_official=False,
        is_leak=True,
    )


def normalize_fortnite_gg(item: dict) -> RawItem | None:
    title = clean_title(item.get("title", ""))
    url = item.get("url", "")
    if not title or not url:
        return None
    # Reject obvious site-chrome links (footer, nav) that may slip past
    # the scraper's URL whitelist. Real cosmetic titles are 3+ words or
    # contain at least one digit/uppercase letter sequence; one-word
    # generic titles like "Contact" / "Privacy" are nav links.
    _NAV_TITLES = {
        "contact", "about", "privacy", "terms", "cookies", "login",
        "signup", "register", "profile", "account", "blog", "news",
        "discord", "faq", "help", "support", "legal", "dmca", "home",
        "shop", "leaks", "stats", "search", "menu",
    }
    if title.strip().lower() in _NAV_TITLES:
        return None
    category = item.get("category", "leaks")
    content = clean_content(item.get("content", ""))
    if _is_past_collab(title, content):
        return None
    return RawItem(
        source=item.get("_source", "fortnite.gg"),
        source_level=2,
        title=title,
        url=url,
        content=content,
        image_url=item.get("image_url", ""),
        category="upcoming_skin" if category == "unreleased" else "skin_leak",
        published_at=_parse_dt(item.get("_fetched_at")),
        is_official=False,
        is_leak=True,
    )


def normalize(item: dict) -> RawItem | None:
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
