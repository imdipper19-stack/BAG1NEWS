"""Title cleanup — strip leaker handles and source attributions.

We position the channel as a primary insider, so user-facing content
must NEVER show "via @ShiinaBR", "(by HYPEX)", "[FireMonkey]" and the
like. This module aggressively strips such tags from raw collected
titles before they are scored, fed to the LLM, or rendered on banners.
"""

from __future__ import annotations

import re

# Leakers / dataminers / fan accounts whose names regularly appear in
# scraped tweet/news titles. Add new ones here as they appear.
_KNOWN_HANDLES = (
    "shiinabr",
    "hypex",
    "firemonkey",
    "ifiremonkey",
    "fortnite gg",
    "fortnitebr",
    "fortnitegame",
    "wensoing",
    "thesquatingdog",
    "leakgg",
    "egoamo",
    "iglooice",
    "luc1d",
    "fnbrunreleased",
    "fnbrnotifier",
    "vastblastn",
    "guille_gag",
    "lokismind",
)

# Patterns that wrap an attribution. We try them in order; first match wins.
_ATTRIB_PATTERNS = [
    # "(via @something)" / "(via something)" / "(by something)"
    re.compile(r"\s*\((?:via|by|source|credit|cred|leak by)[^)]*\)", re.IGNORECASE),
    # "[via @something]" / "[ShiinaBR]"
    re.compile(r"\s*\[(?:via|by|source|credit|cred)?[^\]]*\]", re.IGNORECASE),
    # "via @leaker" / "via leaker" anywhere in the line
    re.compile(r"\s*[—-]?\s*\bvia\s+@?\w[\w\-_]*", re.IGNORECASE),
    # "by @leaker" / "by ShiinaBR"
    re.compile(r"\s*[—-]?\s*\bby\s+@?\w[\w\-_]*", re.IGNORECASE),
    # Standalone "@handle" mentions at the end of the title
    re.compile(r"\s*[—-]?\s*@\w[\w\-_]*"),
    # "(ShiinaBR)" — just parens with a known handle inside
    re.compile(
        r"\s*\((?:" + "|".join(re.escape(h) for h in _KNOWN_HANDLES) + r")\)",
        re.IGNORECASE,
    ),
]

# Phrases that explicitly point at the source ("по данным датамайнеров")
# and add nothing of value to the user-facing copy.
_SOURCE_PHRASES = [
    re.compile(r"по\s+данным\s+датамайнеров[\s,.:;—-]*", re.IGNORECASE),
    re.compile(r"по\s+информации\s+датамайнеров[\s,.:;—-]*", re.IGNORECASE),
    re.compile(r"датамайнер[ыа]?\s+(?:сообщают|пишут|раскрыли|обнаружили)[\s,.:;—-]*", re.IGNORECASE),
    re.compile(r"according\s+to\s+(?:dataminers|leakers)[\s,.:;—-]*", re.IGNORECASE),
    re.compile(r"according\s+to\s+@?\w[\w\-_]*", re.IGNORECASE),
    # "Источник: ..." до конца строки
    re.compile(r"\s*источник\s*:[^\n]*$", re.IGNORECASE),
    # "Source: ..."
    re.compile(r"\s*source\s*:[^\n]*$", re.IGNORECASE),
]


def clean_title(title: str) -> str:
    """Remove leaker attribution from a raw scraped title.

    Examples:
        "New skin (via @ShiinaBR)" → "New skin"
        "First look at Pie Patron skin via @ShiinaBR" → "First look at Pie Patron skin"
        "[FireMonkey] Update detected" → "Update detected"
    """
    if not title:
        return ""
    text = title
    for pat in _ATTRIB_PATTERNS:
        text = pat.sub("", text)
    # Collapse whitespace, strip trailing punctuation that we may have orphaned
    text = re.sub(r"\s+", " ", text).strip(" -—:,.")
    return text


def clean_content(content: str) -> str:
    """Same cleanup for the body text."""
    if not content:
        return ""
    text = content
    for pat in _ATTRIB_PATTERNS:
        text = pat.sub("", text)
    for pat in _SOURCE_PHRASES:
        text = pat.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -—:,.")
    return text


def clean_item_dict(item: dict) -> dict:
    """Mutate a collector-output dict in place: clean title and content."""
    if "title" in item:
        item["title"] = clean_title(item.get("title") or "")
    if "content" in item:
        item["content"] = clean_content(item.get("content") or "")
    return item


# ---------------------------------------------------------------------------
# Russian headline extraction from a finished post body
# ---------------------------------------------------------------------------

# Emoji + symbol prefixes we strip from a banner headline. Keep one
# leading emoji (it adds style to the banner) but drop multiple, brackets,
# bold tags, and pipe-separated brand prefixes like "🔥 ГОРЯЧАЯ УТЕЧКА | ".
_BOLD_TAG_RE = re.compile(r"</?b>|</?strong>", re.IGNORECASE)
_LEADING_PIPE_PREFIX_RE = re.compile(
    r"^\s*[^|\n]{1,40}\|\s*", re.UNICODE
)
_LEADING_EMOJI_RUN_RE = re.compile(
    r"^[\W_]+", re.UNICODE
)


def extract_headline_for_banner(body: str, fallback: str = "") -> str:
    """Pull a clean Russian headline from a generated post body.

    The LLM-generated body always starts with a one-line headline (per
    our prompt). We take that first non-empty line, strip HTML <b>
    tags, drop any "🔥 ГОРЯЧАЯ УТЕЧКА | " brand prefix, and trim to
    fit on a banner.
    """
    if not body:
        return fallback or ""

    # First non-empty line
    first = ""
    for line in body.splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        return fallback or ""

    # Strip <b>...</b> markup and pipe-separated brand prefix
    cleaned = _BOLD_TAG_RE.sub("", first)
    cleaned = _LEADING_PIPE_PREFIX_RE.sub("", cleaned)
    cleaned = cleaned.strip()

    # Cap length so the banner doesn't get a 200-char headline
    if len(cleaned) > 90:
        cleaned = cleaned[:90].rstrip(" ,;:—-") + "…"

    return cleaned or fallback or ""
