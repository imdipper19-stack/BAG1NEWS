"""Relevance scoring engine for Fortnite news items.

Implements spec section 4: 5-component score (0-100) plus boring filter
and publish-decision routing.
"""

from datetime import datetime, timezone

from app.schemas import RawItem

# Spec section 4.3 - boring content filter
BORING_KEYWORDS_EN = [
    "server status",
    "downtime",
    "login issue",
    "matchmaking issue",
    "v-bucks delay",
    "payment issue",
    "item shop issue",
    "maintenance",
    "service outage",
    "service restored",
]
BORING_KEYWORDS_RU = [
    "технические работы",
    "проблемы с входом",
    "матчмейкинг",
    "задержка начисления",
    "сбой магазина",
    "серверы восстановлены",
]

# Spec section 9.4 - high priority keywords
HIGH_PRIORITY_KEYWORDS_EN = [
    "skin",
    "outfit",
    "battle pass",
    "season",
    "chapter",
    "collaboration",
    "collab",
    "free reward",
    "event",
    "live event",
    "leak",
    "datamine",
    "cosmetic",
    "item shop",
    "rare skin",
    "returning skin",
    "new bundle",
    "crew pack",
    "v-bucks",
]
HIGH_PRIORITY_KEYWORDS_RU = [
    "скин",
    "боевой пропуск",
    "сезон",
    "глава",
    "коллаборация",
    "бесплатная награда",
    "ивент",
    "утечка",
    "косметика",
    "магазин предметов",
    "редкий скин",
    "набор",
    "fortnite crew",
    "v-bucks",
]


def is_boring(item: RawItem) -> bool:
    """Return True if the item is boring technical/operational content."""
    text = (item.title + " " + item.content).lower()
    for kw in BORING_KEYWORDS_EN + BORING_KEYWORDS_RU:
        if kw.lower() in text:
            return True
    return False


def _score_relevance(item: RawItem) -> int:
    """0-25: how related the item is to Fortnite skins/season/V-Bucks.

    Leak-tweets are usually short (5-15 words) so naive keyword counting
    underrates them. We give a category-based base so a clearly Fortnite
    leak/cosmetic item starts at a respectable floor and keyword
    matches stack on top of that.
    """
    text = (item.title + " " + item.content).lower()
    matches = 0
    for kw in HIGH_PRIORITY_KEYWORDS_EN + HIGH_PRIORITY_KEYWORDS_RU:
        if kw.lower() in text:
            matches += 1

    # Category-based base score: a confirmed leak/skin item starts at 14.
    if item.category in ("skin_leak", "upcoming_skin"):
        base = 14
    elif item.category in ("leak_discussion", "official_news"):
        base = 12
    elif item.category in ("item_shop", "next_season"):
        base = 12
    elif item.is_leak or "fortnite" in (item.source or "").lower():
        base = 10
    else:
        base = 6

    score = base + matches * 3
    return min(score, 25)


def _score_freshness(item: RawItem) -> int:
    """0-20: how fresh the news is.

    Leaks/upcoming items hold value much longer than a news flash —
    a leak about a future skin is still relevant a week later, since
    it still hasn't released. We use a gentler decay for those.
    """
    if not item.published_at:
        return 12
    now = datetime.now(timezone.utc)
    pub = item.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = (now - pub).total_seconds() / 3600

    is_leak_like = item.is_leak or item.category in (
        "skin_leak", "upcoming_skin", "leak_discussion", "next_season",
    )

    if is_leak_like:
        # Slow decay — leak content stays relevant for ~2 weeks
        if age_hours < 6:
            return 20
        if age_hours < 24:
            return 18
        if age_hours < 72:
            return 15
        if age_hours < 168:   # 1 week
            return 12
        if age_hours < 336:   # 2 weeks
            return 8
        return 4

    # News / shop / official: traditional decay
    if age_hours < 1:
        return 20
    if age_hours < 6:
        return 18
    if age_hours < 24:
        return 14
    if age_hours < 72:
        return 8
    if age_hours < 168:
        return 4
    return 0


def _score_source_trust(item: RawItem) -> int:
    """0-20: trust level based on source_level (1=official … 4=trend).

    Bumped Level 3 (X/Reddit leakers) from 12 to 15 — for our channel
    these are the *primary* signal, not a secondary one. Quality of
    the leak signal is filtered upstream by the past-collab blacklist.
    """
    return {1: 20, 2: 17, 3: 15, 4: 8}.get(item.source_level, 6)


def _score_audience_interest(item: RawItem) -> int:
    """0-25: estimated audience interest (views/discussion potential)."""
    text = (item.title + " " + item.content).lower()
    score = 14
    high_interest_topics = [
        # Sezon / battle pass
        "season", "сезон", "chapter", "глава",
        "battle pass", "боевой пропуск",
        # Collabs (umbrella + frequent IPs)
        "collab", "коллаборация",
        "marvel", "dc", "star wars", "anime", "lego",
        "batman", "spider", "superman", "naruto",
        "disney", "pixar", "harry potter",
        # Live events / specials
        "live event", "live-event", "ивент", "event",
        "showdown", "tournament", "cup", "final",
        # Skin / cosmetic specifics
        "leaked", "leak", "утечка", "новый скин", "new skin",
        "first look", "exclusive",
        # POI / map
        "poi", "map", "карта", "location", "локация",
        # Free stuff
        "free", "бесплатно", "drop",
    ]
    for kw in high_interest_topics:
        if kw in text:
            score += 3
    # Trusted leakers carry inherent interest. We check both `source`
    # (which is sometimes a URL) and the URL itself, since after the
    # round-trip via raw_items the source field gets overwritten with
    # the post URL.
    src = (item.source or "").lower()
    url = (item.url or "").lower()
    haystack = src + " " + url
    if any(name in haystack for name in (
        "hypex", "shiinabr", "firemonkey", "ifiremonkey",
        "blortzen", "drcacahuette", "wensoing", "fnbrunreleased",
        "fortnitestatus", "fnbrnotifier",
    )):
        score += 6
    # x.com / twitter URLs are typically leaker tweets even if we
    # didn't recognise the handle.
    if "x.com/" in url or "twitter.com/" in url or "/r/fortnitelea" in url:
        score += 3
    # Leak/upcoming items deserve a baseline boost — they're inherently
    # the most clickable content for our audience.
    if item.category in ("skin_leak", "upcoming_skin", "leak_discussion"):
        score += 3
    return min(score, 25)


def _score_monetization_fit(item: RawItem) -> int:
    """0-10: how well a V-Bucks shop CTA fits this item."""
    text = (item.title + " " + item.content).lower()
    monetization_keywords = [
        "skin",
        "скин",
        "shop",
        "магазин",
        "bundle",
        "набор",
        "battle pass",
        "боевой пропуск",
        "v-bucks",
        "вбакс",
        "crew",
        "season",
        "сезон",
    ]
    matches = sum(1 for kw in monetization_keywords if kw in text)
    return min(matches * 2, 10)


def score_item(item: RawItem) -> dict:
    """Score an item 0-100 across 5 components.

    Returns:
        dict with keys: total, relevance, freshness, source_trust,
        audience_interest, monetization_fit, is_boring
    """
    if is_boring(item):
        return {
            "total": 0,
            "relevance": 0,
            "freshness": 0,
            "source_trust": 0,
            "audience_interest": 0,
            "monetization_fit": 0,
            "is_boring": True,
        }

    relevance = _score_relevance(item)
    freshness = _score_freshness(item)
    source_trust = _score_source_trust(item)
    audience_interest = _score_audience_interest(item)
    monetization_fit = _score_monetization_fit(item)
    total = relevance + freshness + source_trust + audience_interest + monetization_fit

    return {
        "total": total,
        "relevance": relevance,
        "freshness": freshness,
        "source_trust": source_trust,
        "audience_interest": audience_interest,
        "monetization_fit": monetization_fit,
        "is_boring": False,
    }


def should_publish(score: int, min_score: int | None = None) -> str:
    """Decide publication action based on score.

    Returns one of: "immediate", "conditional", "digest", "skip".

    The thresholds anchor on ``min_score`` (the runtime-configurable
    minimum score to publish, defaults to 70). A post is:
      * "immediate"   if score >= min_score + 15  (very strong signal)
      * "conditional" if score >= min_score       (publish, queue for review)
      * "digest"      if score >= min_score - 20  (aggregate later)
      * "skip"        otherwise

    Lowering ``min_score`` (e.g. via ``/score 50``) automatically lowers
    the immediate/digest cutoffs in step.
    """
    if min_score is None:
        min_score = 70
    if score >= min_score + 15:
        return "immediate"
    if score >= min_score:
        return "conditional"
    if score >= max(0, min_score - 20):
        return "digest"
    return "skip"
