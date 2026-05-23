"""Fact/leak verification service.

Per spec section R5 (Leak Verification):
    - Epic/Fortnite official sources (source_level=1) → mark as official
    - Fortnite-API / Fortnite.GG (source_level=2) → data-based, not confirmed
    - HYPEX / ShiinaBR / FireMonkey (source_level=3) → leak
    - Reddit r/FortniteLeaks (source_level=3) → supplementary leak only
    - YouTube content creators (source_level=4) → trend signal, not source of truth
    - Leaks require ≥2 sources OR presence in Fortnite-API/Fortnite.GG.

Per spec section R6:
    Leak posts must include: "Epic Games пока не подтверждала эту информацию"
"""

from __future__ import annotations

import re
from app.schemas import RawItem

OFFICIAL_LABEL = "Официальная новость"
LEAK_DISCLAIMER = "Epic Games пока не подтверждала эту информацию"


def get_official_label() -> str:
    """Return the Russian label for officially confirmed news."""
    return OFFICIAL_LABEL


def get_leak_disclaimer() -> str:
    """Return the Russian disclaimer to attach to leak posts.

    The exact wording is mandated by spec R6.
    """
    return LEAK_DISCLAIMER


def verify_item(item: RawItem) -> RawItem:
    """Apply source-level rules to set ``is_official`` / ``is_leak`` flags.

    Returns a *copy* of the item with corrected flags; the original is not
    mutated. This keeps the pipeline functional and easier to test.

    Rules:
      - source_level == 1 → official  (is_official=True,  is_leak=False)
      - source_level == 2 → data-based; not official, not necessarily a leak.
        We respect whatever the upstream collector said about ``is_leak``
        (e.g. fortnite.gg/leaks pages legitimately come in as is_leak=True).
      - source_level == 3 → leak       (is_official=False, is_leak=True)
      - source_level >= 4 → trend signal; clear both flags
    """
    new = item.model_copy(deep=True)

    if item.source_level == 1:
        new.is_official = True
        new.is_leak = False
    elif item.source_level == 2:
        new.is_official = False
        # keep collector's is_leak (e.g. fortnite.gg/leaks → True, /shop → False)
    elif item.source_level == 3:
        new.is_official = False
        new.is_leak = True
    else:
        # level 4+ → trend / unknown: not authoritative either way
        new.is_official = False
        new.is_leak = False

    return new


# ---------------------------------------------------------------------------
# Cross-source confirmation
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[^\w\u0400-\u04FF]+", re.UNICODE)


def _normalize_words(text: str) -> set[str]:
    """Extract significant word tokens from text for similarity comparison.

    Lowercases, splits on non-word/non-Cyrillic chars, drops short tokens.
    """
    if not text:
        return set()
    cleaned = _TOKEN_RE.sub(" ", text.lower())
    tokens = cleaned.split()
    # Filter out very short tokens (articles, particles, fragments)
    return {t for t in tokens if len(t) > 3}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets. Returns 0.0 for two empty sets."""
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def check_cross_source_confirmation(
    item: RawItem,
    all_items: list[RawItem],
    threshold: float = 0.4,
) -> bool:
    """Return True if at least 2 *different* sources cover the same story.

    Uses Jaccard similarity on title tokens to detect related items.

    Per spec R5: "Leaks require ≥2 sources OR presence in
    Fortnite-API/Fortnite.GG". This function handles the "≥2 sources" half;
    the API/GG side is already covered by ``verify_item`` keeping
    ``is_leak`` truthful for level-2 collectors.
    """
    target_tokens = _normalize_words(item.title)
    if not target_tokens:
        return False

    confirming_sources = {item.source}
    for other in all_items:
        # Skip the same record (same URL) and the same source (we want
        # confirmation from *different* sources).
        if other.url == item.url:
            continue
        if other.source == item.source:
            continue

        other_tokens = _normalize_words(other.title)
        if _jaccard(target_tokens, other_tokens) >= threshold:
            confirming_sources.add(other.source)
            if len(confirming_sources) >= 2:
                return True

    return False
