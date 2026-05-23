"""Celery tasks.

Each collector task runs the async collector + persists raw items.
process_queue scores, verifies, writes, generates an image, and publishes
items that pass the score threshold and daily-limit checks.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bot.admin_commands import is_emergency_stopped
from app.celery_app import celery_app
from app.collectors import (
    fortnite_api,
    fortnite_gg,
    fortnite_news,
    leaks_x,
    reddit,
    youtube,
)
from app.config import settings
from app.db.models import Post as PostORM, RawItem as RawItemORM
from app.db.session import get_session
from app.schemas import RawItem
from app.services import normalizer
from app.services.dedup import DuplicateChecker
from app.services.image_generator import ImageGenerator
from app.services.publisher import TelegramPublisher
from app.services.scoring import score_item, should_publish
from app.services.verifier import verify_item
from app.services.writer import write_post

logger = logging.getLogger(__name__)

DAILY_POST_KEY_PREFIX = "fortnite_bot:posts_today"


# -------------------------- Helpers --------------------------

def _run_async(coro: Awaitable):
    """Run an async coroutine from a sync Celery task."""
    return asyncio.run(coro)


async def _persist_items(items: list[RawItem]) -> int:
    """Insert raw items into the DB. Returns count actually inserted."""
    inserted = 0
    if not items:
        return 0
    async with get_session() as session:
        for item in items:
            row = RawItemORM(
                title=item.title,
                url=item.url,
                content=item.content,
                image_url=item.image_url,
                category=item.category,
                is_official=item.is_official,
                is_leak=item.is_leak,
                published_at=item.published_at,
            )
            session.add(row)
            try:
                await session.flush()
                inserted += 1
            except IntegrityError:
                # Unique URL constraint — already in DB
                await session.rollback()
                continue
    return inserted


async def _normalize_and_dedup(raw_dicts: list[dict]) -> list[RawItem]:
    """Normalize collector output and remove duplicates."""
    normalized: list[RawItem] = []
    for raw in raw_dicts:
        item = normalizer.normalize(raw)
        if item is not None:
            normalized.append(item)

    checker = DuplicateChecker()
    try:
        unique = await checker.filter_duplicates(normalized)
    finally:
        await checker.close()
    return unique


async def _run_collector_pipeline(
    name: str, fetch_fn: Callable[[], Awaitable[list[dict]]]
) -> int:
    """Generic: fetch → normalize → dedup → persist."""
    logger.info("Collector %s starting", name)
    raw = await fetch_fn()
    logger.info("Collector %s fetched %d raw items", name, len(raw))
    if not raw:
        return 0
    unique = await _normalize_and_dedup(raw)
    inserted = await _persist_items(unique)
    logger.info("Collector %s persisted %d new items", name, inserted)
    return inserted


# -------------------------- Daily limit --------------------------

def _today_key() -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{DAILY_POST_KEY_PREFIX}:{today}"


async def _can_publish_today() -> bool:
    """Return True if we are below the daily post limit."""
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        count_raw = await r.get(_today_key())
        count = int(count_raw) if count_raw else 0
        return count < settings.max_posts_per_day
    finally:
        await r.aclose()


async def _increment_daily_counter() -> int:
    """Increment today's post counter (with 48h TTL) and return the new value."""
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        new_value = await r.incr(_today_key())
        await r.expire(_today_key(), 48 * 60 * 60)
        return int(new_value)
    finally:
        await r.aclose()


# -------------------------- Collector tasks --------------------------

@celery_app.task(name="app.tasks.collect_fortnite_api")
def collect_fortnite_api() -> int:
    async def run() -> int:
        total = 0
        for fn in (fortnite_api.fetch_news, fortnite_api.fetch_new_cosmetics):
            total += await _run_collector_pipeline("fortnite_api", fn)
        return total
    return _run_async(run())


@celery_app.task(name="app.tasks.collect_shop")
def collect_shop() -> int:
    return _run_async(_run_collector_pipeline("fortnite_api/shop", fortnite_api.fetch_shop))


@celery_app.task(name="app.tasks.collect_fortnite_news")
def collect_fortnite_news() -> int:
    return _run_async(_run_collector_pipeline("fortnite_news", fortnite_news.fetch_official_news))


@celery_app.task(name="app.tasks.collect_youtube")
def collect_youtube() -> int:
    return _run_async(_run_collector_pipeline("youtube", youtube.fetch_youtube_videos))


@celery_app.task(name="app.tasks.collect_leaks")
def collect_leaks() -> int:
    return _run_async(_run_collector_pipeline("leaks_x", leaks_x.fetch_leak_sources))


@celery_app.task(name="app.tasks.collect_reddit")
def collect_reddit() -> int:
    return _run_async(_run_collector_pipeline("reddit", reddit.fetch_reddit_leaks))


@celery_app.task(name="app.tasks.collect_fortnite_gg")
def collect_fortnite_gg() -> int:
    return _run_async(_run_collector_pipeline("fortnite_gg", fortnite_gg.fetch_fortnite_gg))


# -------------------------- Processing queue --------------------------

@celery_app.task(name="app.tasks.process_queue")
def process_queue() -> int:
    """Pick fresh raw_items, score, verify, write, generate image, publish."""
    return _run_async(_process_queue_async())


async def _process_queue_async() -> int:
    if await is_emergency_stopped():
        logger.info("Emergency stop is active — skipping process_queue")
        return 0

    if not await _can_publish_today():
        logger.info("Daily post limit reached — skipping process_queue")
        return 0

    # Fetch up to 30 candidate items: those without an associated post
    async with get_session() as session:
        stmt = (
            select(RawItemORM)
            .outerjoin(PostORM, PostORM.raw_item_id == RawItemORM.id)
            .where(PostORM.id.is_(None))
            .order_by(RawItemORM.id.desc())
            .limit(30)
        )
        result = await session.execute(stmt)
        rows: list[RawItemORM] = list(result.scalars())

    if not rows:
        return 0

    publisher = TelegramPublisher() if not settings.require_admin_approval else None
    image_gen = ImageGenerator()
    published_count = 0

    for row in rows:
        # Build a RawItem from ORM row
        item = RawItem(
            source=row.url or "unknown",  # source not stored separately; placeholder
            source_level=1 if row.is_official else (3 if row.is_leak else 2),
            title=row.title or "",
            url=row.url or "",
            content=row.content or "",
            image_url=row.image_url or "",
            category=row.category or "general",
            published_at=row.published_at,
            is_official=bool(row.is_official),
            is_leak=bool(row.is_leak),
        )

        verified = verify_item(item)
        score_dict = score_item(verified)
        decision = should_publish(score_dict["total"])

        # Persist a Post row regardless (for traceability)
        async with get_session() as session:
            post_row = PostORM(
                raw_item_id=row.id,
                title=verified.title,
                body="",
                image_prompt="",
                image_url=verified.image_url,
                score=score_dict["total"],
                status=decision,
            )
            session.add(post_row)
            await session.flush()
            post_db_id = post_row.id

        if decision not in ("immediate", "conditional"):
            continue

        if score_dict["total"] < settings.min_score_to_publish:
            continue

        if not await _can_publish_today():
            logger.info("Reached daily limit during processing — stopping")
            break

        # Generate post body via LLM
        body = await write_post(verified)
        if not body:
            continue

        # Generate image — always render a Fortnite-style banner, and pass
        # the source image (e.g. real skin icon from Fortnite-API) as the
        # reference so the actual character appears on the banner.
        image_to_send = await image_gen.generate_news_banner(
            topic=f"Fortnite — {verified.title}",
            headline=verified.title,
            reference_image_url=row.image_url or None,
        )
        if not image_to_send:
            logger.warning("No image available for %s — skipping", verified.title)
            continue

        message_id = None
        if settings.require_admin_approval:
            # Park the post in pending_approval state and DM the admin.
            async with get_session() as session:
                stmt2 = select(PostORM).where(PostORM.id == post_db_id)
                res = await session.execute(stmt2)
                post = res.scalar_one_or_none()
                if post is not None:
                    post.status = "pending_approval"
                    post.body = body
                    if image_to_send and not str(image_to_send).startswith("http"):
                        post.image_url = image_to_send
                    await session.flush()

            # Local import avoids circular dependency at module load
            from app.services.approval import send_for_approval
            sent = await send_for_approval(post_db_id)
            if sent:
                logger.info("Post %s queued for admin approval", post_db_id)
            # We don't increment daily counter or mark as published yet —
            # that happens when the admin clicks ✅.
        else:
            # Auto-publish mode (use after the bot is calibrated)
            if publisher is None:
                publisher = TelegramPublisher()
            message_id = await publisher.publish_post(
                body=body,
                image_path_or_url=image_to_send,
                post_id=post_db_id,
            )
            if message_id:
                published_count += 1
                await _increment_daily_counter()
                async with get_session() as session:
                    stmt2 = select(PostORM).where(PostORM.id == post_db_id)
                    res = await session.execute(stmt2)
                    post = res.scalar_one_or_none()
                    if post is not None:
                        post.status = "published"
                        post.body = body
                        if image_to_send and not str(image_to_send).startswith("http"):
                            post.image_url = image_to_send
                        await session.flush()

    return published_count


# -------------------------- Digest tasks --------------------------

@celery_app.task(name="app.tasks.daily_shop_digest")
def daily_shop_digest() -> dict:
    """Build today's shop digest and queue it for approval."""
    return _run_async(_daily_shop_digest_async())


async def _daily_shop_digest_async() -> dict:
    if await is_emergency_stopped():
        logger.info("Emergency stop active — skipping daily_shop_digest")
        return {"skipped": "emergency_stop"}

    from app.services.digest import build_shop_digest
    summary = await build_shop_digest(dry_run=False, top_n=5)

    post_id = summary.get("post_id")
    if post_id:
        from app.services.approval import send_for_approval
        await send_for_approval(post_id)

    return summary


@celery_app.task(name="app.tasks.weekly_leaks_digest")
def weekly_leaks_digest() -> dict:
    """Build the weekly leaks roundup and queue it for approval."""
    return _run_async(_weekly_leaks_digest_async())


async def _weekly_leaks_digest_async() -> dict:
    if await is_emergency_stopped():
        return {"skipped": "emergency_stop"}

    from app.services.digest import build_leaks_digest
    summary = await build_leaks_digest(dry_run=False, top_n=5)

    post_id = summary.get("post_id")
    if post_id:
        from app.services.approval import send_for_approval
        await send_for_approval(post_id)

    return summary


# -------------------- Themed weekly series --------------------

@celery_app.task(name="app.tasks.engagement_poll")
def engagement_poll() -> dict:
    """Publish an engagement poll. Runs every 3 days."""
    return _run_async(_engagement_poll_async())


async def _engagement_poll_async() -> dict:
    if await is_emergency_stopped():
        return {"skipped": "emergency_stop"}
    from app.services.polls import build_and_queue_poll
    return await build_and_queue_poll()


@celery_app.task(name="app.tasks.monday_shop_recap")
def monday_shop_recap() -> dict:
    """Monday post: 'Shop highlights of the past week'."""
    return _run_async(_monday_shop_recap_async())


async def _monday_shop_recap_async() -> dict:
    """Reuses the da
ily shop digest pipeline but with a wider time window."""
    if await is_emergency_stopped():
        return {"skipped": "emergency_stop"}
    from app.services.digest import build_shop_digest
    summary = await build_shop_digest(dry_run=False, top_n=7)
    post_id = summary.get("post_id")
    if post_id:
        from app.services.approval import send_for_approval
        await send_for_approval(post_id)
    return summary


@celery_app.task(name="app.tasks.wednesday_official_roundup")
def wednesday_official_roundup() -> dict:
    """Wednesday post: 'What Epic announced this week'."""
    return _run_async(_wednesday_official_async())


async def _wednesday_official_async() -> dict:
    """Top official-source items from the last 7 days, packaged as a digest."""
    if await is_emergency_stopped():
        return {"skipped": "emergency_stop"}
    # We re-use the leaks digest path but bias it to official sources.
    # A cleaner implementation would be a dedicated build_official_digest;
    # for now we read the same items but pre-filter by is_official.
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, and_

    from app.db.models import Post as PostORM
    from app.db.session import get_session
    from app.db.models import RawItem as RawItemORM
    from app.schemas import RawItem as RawItemSchema
    from app.services.scoring import score_item
    from app.services.image_generator import ImageGenerator
    from app.services.writer import LLMClient
    from app.services.digest import _format_items_for_prompt, _row_to_rawitem
    from app.config import settings

    cutoff = datetime.utcnow() - timedelta(days=7)
    async with get_session() as session:
        stmt = (
            select(RawItemORM)
            .where(and_(
                RawItemORM.created_at >= cutoff,
                RawItemORM.is_official.is_(True),
            ))
            .order_by(RawItemORM.id.desc())
            .limit(60)
        )
        rows = list((await session.execute(stmt)).scalars())

    summary: dict = {"kind": "official_roundup", "candidates": len(rows), "post_id": None}
    if not rows:
        return summary

    scored = []
    for r in rows:
        item = _row_to_rawitem(r)
        scored.append((item, score_item(item)["total"]))
    scored.sort(key=lambda t: t[1], reverse=True)
    selected = [it for it, _ in scored[:5]]

    items_block = _format_items_for_prompt(selected, limit=5)
    base = (
        "⚡️ <b>EPIC ЗА НЕДЕЛЮ</b>\n\n"
        "Главные официальные новости Fortnite за последние 7 дней:\n\n"
        f"{items_block}\n\n"
        f"💳 Пополнить V-Bucks: {settings.shop_url}\n\n"
        "#Fortnite #ФортнайтНовости #Epic"
    )
    client = LLMClient()
    polished = await client.chat(
        messages=[
            {"role": "system", "content": (
                "Ты редактор русскоязычного Telegram-канала о Fortnite. "
                "Пиши в официальном новостном стиле без кликбейта."
            )},
            {"role": "user", "content": (
                "Перепиши этот пост чуть более естественно, сохрани "
                "структуру, не выдумывай. Верни только текст:\n\n" + base
            )},
        ],
        temperature=0.5,
        max_tokens=900,
    )
    body = polished.strip() if polished else base

    refs = [it.image_url for it in selected if it.image_url][:1]
    img_gen = ImageGenerator()
    image_path = await img_gen.generate_news_banner(
        topic="Fortnite weekly official news roundup",
        headline="EPIC ЗА НЕДЕЛЮ",
        style="news",
        reference_image_url=refs[0] if refs else None,
    )

    async with get_session() as session:
        post = PostORM(
            raw_item_id=None,
            title="EPIC ЗА НЕДЕЛЮ",
            body=body,
            image_prompt="official_roundup",
            image_url=image_path or "",
            score=85,
            status="pending_approval",
        )
        session.add(post)
        await session.flush()
        summary["post_id"] = post.id

    if summary["post_id"]:
        from app.services.approval import send_for_approval
        await send_for_approval(summary["post_id"])
    return summary


@celery_app.task(name="app.tasks.sunday_deals")
def sunday_deals() -> dict:
    """Sunday post: 'What's worth buying today'.

    Grabs current shop items and uses the LLM to recommend a small
    selection (up to 3) based on rarity, return-rate, and price.
    """
    return _run_async(_sunday_deals_async())


async def _sunday_deals_async() -> dict:
    if await is_emergency_stopped():
        return {"skipped": "emergency_stop"}
    from app.services.digest import build_shop_digest
    # Uses the digest builder but with a Sunday-specific topic; for now
    # we reuse the daily digest with a smaller item count.
    summary = await build_shop_digest(dry_run=False, top_n=3)
    post_id = summary.get("post_id")
    if post_id:
        from app.services.approval import send_for_approval
        await send_for_approval(post_id)
    return summary
