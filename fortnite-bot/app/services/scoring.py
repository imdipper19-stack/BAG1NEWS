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
    """0-25: how related the item is to Fortnite skins/season/V-Bucks."""
    text = (item.title + " " + item.content).lower()
    matches = 0
    for kw in HIGH_PRIORITY_KEYWORDS_EN + HIGH_PRIORITY_KEYWORDS_RU:
        if kw.lower() in text:
            matches += 1
    if matches == 0:
        return 5
    if matches >= 5:
        return 25
    return 5 + matches * 4


def _score_freshness(item: RawItem) -> int:
    """0-20: how fresh the news is."""
    if not item.published_at:
        return 10
    now = datetime.now(timezone.utc)
    pub = item.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = (now - pub).total_seconds() / 3600
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
    """0-20: trust level based on source_level (1=official … 4=trend)."""
    return {1: 20, 2: 16, 3: 12, 4: 6}.get(item.source_level, 5)


def _score_audience_interest(item: RawItem) -> int:
    """0-25: estimated audience interest (views/discussion potential)."""
    text = (item.title + " " + item.content).lower()
    score = 10
    high_interest_topics = [
        "season",
        "сезон",
        "battle pass",
        "боевой пропуск",
        "collab",
        "коллаборация",
        "marvel",
        "star wars",
        "anime",
        "live event",
        "live-event",
    ]
    for kw in high_interest_topics:
        if kw in text:
            score += 4
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


def should_publish(score: int) -> str:
    """Decide publication action based on score.

    Returns one of: "immediate", "conditional", "digest", "skip".
    """
    if score >= 85:
        return "immediate"
    if score >= 70:
        return "conditional"
    if score >= 50:
        return "digest"
    return "skip"
