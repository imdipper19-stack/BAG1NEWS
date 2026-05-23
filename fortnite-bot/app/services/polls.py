"""Engagement polls.

Every 3 days the bot publishes a poll to the channel — Telegram boosts
posts with interactions, and we get a free signal about which content
the audience cares about.

Strategy:
  1. Pick top leaks/upcoming-skins from the last 7 days that have
     distinct titles. If we have 4+ → post a "what skin do you want
     most?" poll using their names.
  2. Otherwise → fall back to a generic seasonal poll from a curated
     pool so the cadence never breaks.
  3. Every poll is queued through the same admin-approval DM flow as
     regular posts (the admin sees the question + options, hits ✅).

Polls are anonymous (anyone in the channel can vote) and non-quiz
(multiple options can win).
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from app.bot.telegram import get_bot
from app.config import settings
from app.db.models import Post as PostORM, RawItem as RawItemORM
from app.db.session import get_session

logger = logging.getLogger(__name__)


# Curated fallback questions — used when there isn't enough fresh leak
# data to build a personalised poll. One pick per run.
_FALLBACK_POLLS: list[tuple[str, list[str]]] = [
    (
        "🎮 Какой сезон Fortnite вам понравился больше всего?",
        ["Глава 2 / Сезон 7 (Cube)", "Глава 4 / Сезон 4 (Heist)",
         "Fortnite OG", "Глава 5", "Текущий"],
    ),
    (
        "🔥 Какая коллаборация Fortnite самая крутая?",
        ["Marvel", "Star Wars", "Аниме (Naruto / DBZ)",
         "Музыкальные исполнители", "Игры (Halo, Doom)"],
    ),
    (
        "💳 На что бы потратили 5000 V-Bucks прямо сейчас?",
        ["Боевой пропуск", "Новый набор скинов",
         "Редкое возвращение", "Подождал бы скидки",
         "Накопил бы дальше"],
    ),
    (
        "👀 Что вам важнее в новом сезоне?",
        ["Новые скины и боевой пропуск", "Изменения карты",
         "Live-event и сюжет", "Новые механики и оружие",
         "Коллаборации"],
    ),
    (
        "🛒 Как часто вы заходите в магазин Fortnite?",
        ["Каждый день", "Несколько раз в неделю",
         "Только когда есть редкие скины", "Редко",
         "Только во время сезонных событий"],
    ),
]

POLL_COOLDOWN_DAYS = 3
LEAK_LOOKBACK_DAYS = 7


async def _last_poll_age_days() -> Optional[float]:
    """How many days since the last poll-post we sent? None if never."""
    async with get_session() as session:
        result = await session.execute(
            select(PostORM)
            .where(PostORM.image_prompt == "poll")
            .order_by(PostORM.id.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last is None or last.created_at is None:
            return None
        # created_at is stored as naive UTC; treat it as such.
        last_dt = last.created_at
        if last_dt.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=None)
        delta = datetime.utcnow() - last_dt
        return delta.total_seconds() / 86400


def _clean_skin_title(title: str) -> str:
    """Strip leak-prefix noise so "Утечка: новый скин X" becomes "X"."""
    s = title.strip()
    for prefix in (
        "утечка:", "утечка ", "leaked:", "leaked ", "по данным датамайнеров",
        "в файлах:", "новый скин:", "новый скин ", "скин:",
    ):
        low = s.lower()
        if low.startswith(prefix):
            s = s[len(prefix):].strip(" :—-")
            break
    # Collapse to first ~40 chars / sentence boundary
    if len(s) > 60:
        cut = s[:60].rsplit(" ", 1)[0]
        s = cut
    return s or title


async def _build_personalised_poll() -> tuple[str, list[str]] | None:
    """If we have enough recent leaks with distinct titles → a custom poll."""
    # naive UTC to match Postgres TIMESTAMP WITHOUT TIME ZONE
    cutoff = datetime.utcnow() - timedelta(days=LEAK_LOOKBACK_DAYS)
    async with get_session() as session:
        stmt = (
            select(RawItemORM)
            .where(
                RawItemORM.created_at >= cutoff,
                RawItemORM.is_leak.is_(True),
            )
            .order_by(RawItemORM.id.desc())
            .limit(60)
        )
        result = await session.execute(stmt)
        rows: list[RawItemORM] = list(result.scalars())

    seen: set[str] = set()
    options: list[str] = []
    for row in rows:
        if not row.title:
            continue
        cleaned = _clean_skin_title(row.title)
        key = cleaned.lower()[:40]
        if key in seen:
            continue
        seen.add(key)
        options.append(cleaned)
        if len(options) >= 5:
            break

    if len(options) < 4:
        return None

    return (
        "🔥 Какой из утечённых скинов вам нужен сильнее всего?",
        options[:5],
    )


async def build_poll() -> dict:
    """Pick a poll question + options. Returns a dict ready to publish."""
    personalised = await _build_personalised_poll()
    if personalised:
        question, options = personalised
        kind = "leak_poll"
    else:
        question, options = random.choice(_FALLBACK_POLLS)
        kind = "fallback_poll"

    # Telegram poll constraints:
    #   * up to 12 options
    #   * each option max 100 chars
    options = [opt[:100] for opt in options][:12]

    return {
        "question": question[:300],
        "options": options,
        "kind": kind,
    }


async def publish_poll_to_channel(question: str, options: list[str]) -> Optional[int]:
    """Send the poll to the configured channel. Returns message_id."""
    bot = get_bot()
    try:
        message = await bot.send_poll(
            chat_id=settings.telegram_channel_id,
            question=question,
            options=options,
            is_anonymous=True,
            allows_multiple_answers=False,
        )
    except TelegramAPIError as e:
        logger.error("Failed to publish poll: %s", e)
        return None
    return message.message_id


async def send_poll_for_approval(question: str, options: list[str], post_id: int) -> bool:
    """DM the admin a preview of the poll with approve/reject buttons.

    Telegram DMs don't accept polls directly inside other messages, so
    we send the question + options as text plus the same approve/reject
    keyboard used for image posts. On approval the actual poll is then
    sent to the channel via the standalone send_poll endpoint.
    """
    if not settings.telegram_admin_user_id:
        logger.warning("TELEGRAM_ADMIN_USER_ID not set — poll cannot be sent for approval")
        return False

    bot = get_bot()
    options_block = "\n".join(f"• {o}" for o in options)
    text = (
        f"<b>На модерацию (ОПРОС)</b> · post #{post_id}\n\n"
        f"<b>{question}</b>\n\n{options_block}\n\n"
        f"При одобрении опрос уйдёт в {settings.telegram_channel_id}."
    )

    # Re-use the existing approval keyboard
    from app.services.approval import _approval_keyboard  # type: ignore
    try:
        await bot.send_message(
            chat_id=settings.telegram_admin_user_id,
            text=text,
            reply_markup=_approval_keyboard(post_id),
        )
    except TelegramAPIError as e:
        logger.error("Failed to DM admin (poll preview): %s", e)
        return False
    return True


async def build_and_queue_poll() -> dict:
    """Public entrypoint: build a poll, save as pending Post, DM admin."""
    summary: dict = {"kind": None, "post_id": None, "question": "", "delivered": False}

    # Cooldown check
    age = await _last_poll_age_days()
    if age is not None and age < POLL_COOLDOWN_DAYS:
        logger.info("Poll cooldown: last poll %.1f days ago, skipping", age)
        summary["skipped"] = "cooldown"
        return summary

    poll = await build_poll()
    summary["kind"] = poll["kind"]
    summary["question"] = poll["question"]

    # Persist as a Post in pending_approval state. Body holds the rendered
    # text preview, image_prompt='poll' marks this row as a poll for the
    # cooldown query, and the actual poll content is encoded in body.
    body_payload = (
        f"POLL_QUESTION::{poll['question']}\n"
        f"POLL_OPTIONS::" + "\n".join(poll["options"])
    )
    async with get_session() as session:
        post = PostORM(
            raw_item_id=None,
            title=poll["question"][:200],
            body=body_payload,
            image_prompt="poll",  # marker for cooldown lookups
            image_url="",
            score=80,
            status="pending_approval",
        )
        session.add(post)
        await session.flush()
        summary["post_id"] = post.id

    delivered = await send_poll_for_approval(
        poll["question"], poll["options"], summary["post_id"]
    )
    summary["delivered"] = delivered
    return summary


async def approve_poll(post_id: int) -> Optional[int]:
    """Called by the approval handler when admin clicks ✅ on a poll preview.

    Reads the encoded question/options from the Post row, sends the
    actual poll to the channel, marks Post as published.
    """
    async with get_session() as session:
        result = await session.execute(select(PostORM).where(PostORM.id == post_id))
        post = result.scalar_one_or_none()
        if post is None or post.image_prompt != "poll":
            return None
        body = post.body or ""

    question = ""
    options: list[str] = []
    for line in body.splitlines():
        if line.startswith("POLL_QUESTION::"):
            question = line[len("POLL_QUESTION::"):]
        elif line.startswith("POLL_OPTIONS::"):
            options.append(line[len("POLL_OPTIONS::"):])
        elif options:
            options.append(line)
    if not question or len(options) < 2:
        return None

    message_id = await publish_poll_to_channel(question, options)
    if message_id is None:
        return None

    async with get_session() as session:
        result = await session.execute(select(PostORM).where(PostORM.id == post_id))
        post = result.scalar_one_or_none()
        if post is not None:
            post.status = "published"
            await session.flush()
    return message_id
