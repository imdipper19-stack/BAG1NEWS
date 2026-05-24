"""One-off migration: refresh CTA on already-published posts.

Iterates over `published_posts`, pulls the fresh body from `posts.body`
(which still contains the old V-Bucks CTA), rewrites:
  - "💳 ... V-Bucks ...: <url>" → "💎 Магазин для игроков: <new_url>"
  - any leftover `https://bag1v-bucks.shop/`  → `https://bag1-v-bucks.shop/`
  - removes the old inline keyboard

Run:
  docker compose exec -T worker python -m scripts.update_published_ctas
"""

import asyncio
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from app.bot.telegram import get_bot, close_bot
from app.config import settings
from app.db.models import Post, PublishedPost
from app.db.session import get_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("update_ctas")

# ---------------------------------------------------------------------------
# Body rewrite rules
# ---------------------------------------------------------------------------

NEW_URL = settings.shop_url
NEW_CTA_LINE = f"💎 Магазин для игроков: {NEW_URL}"

# Lines we want to replace (each pattern is matched line-by-line).
LINE_PATTERNS = [
    # "💳 Пополнить V-Bucks можно на нашем сайте:" / "💳 V-Bucks: ..." / similar
    re.compile(r"^.*💳[^\n]*v-bucks[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*💳[^\n]*v\xa0bucks[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*💳[^\n]*$"),
    re.compile(r"^.*Пополнить\s+V-Bucks[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*V-Bucks[^\n]*по\s+выгодному\s+курсу[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*Магазин\s+V-Bucks[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*V-Bucks\s+доступны[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*V-Bucks[^\n]*на\s+нашем\s+сайте[^\n]*$", re.IGNORECASE),
    re.compile(r"^.*Подготовиться\s+к\s+новым\s+скинам[^\n]*$", re.IGNORECASE),
]


def rewrite_body(body: str) -> str:
    """Replace old V-Bucks CTA lines with the new shop CTA."""
    if not body:
        return body
    lines = body.splitlines()
    out: list[str] = []
    cta_inserted = False

    for line in lines:
        # Replace any standalone old shop URL with the new one
        line = line.replace("https://bag1v-bucks.shop/", NEW_URL)

        if any(p.search(line) for p in LINE_PATTERNS):
            # Drop this line; we'll insert the canonical CTA once.
            if not cta_inserted:
                out.append(NEW_CTA_LINE)
                cta_inserted = True
            continue

        # Replace the URL on its own line just below the dropped intro
        if line.strip() == NEW_URL or line.strip() == "https://bag1v-bucks.shop/":
            # Already covered by the inserted CTA — skip.
            continue

        out.append(line)

    if not cta_inserted:
        # Body had no CTA at all — append it at the end.
        out.append("")
        out.append(NEW_CTA_LINE)

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Telegram caption length limit: 1024 chars
# ---------------------------------------------------------------------------

CAPTION_MAX = 1024


def trim_caption(text: str) -> str:
    if len(text) <= CAPTION_MAX:
        return text
    # Trim with a marker but try to keep the CTA intact at the end.
    head, sep, tail = text.partition(NEW_CTA_LINE)
    if sep:
        # Reserve room for sep+tail
        budget = CAPTION_MAX - len(sep) - len(tail) - 1
        if budget > 50:
            return head[:budget].rstrip() + "\n" + sep + tail
    return text[: CAPTION_MAX - 1] + "…"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def update_one(bot: Bot, channel_id: str, message_id: int, body: str) -> str:
    new_body = rewrite_body(body)
    new_body = trim_caption(new_body)
    try:
        await bot.edit_message_caption(
            chat_id=channel_id,
            message_id=message_id,
            caption=new_body,
            reply_markup=None,
        )
        return "ok"
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            # Try to refresh just the keyboard
            try:
                await bot.edit_message_reply_markup(
                    chat_id=channel_id,
                    message_id=message_id,
                    reply_markup=None,
                )
                return "kb-only"
            except TelegramBadRequest as e2:
                if "message is not modified" in str(e2).lower():
                    return "noop"
                raise
        return f"error: {e}"


async def main() -> None:
    async with get_session() as session:
        stmt = (
            select(PublishedPost, Post.body)
            .join(Post, Post.id == PublishedPost.post_id)
            .order_by(PublishedPost.id.asc())
        )
        rows = list((await session.execute(stmt)).all())

    log.info("Found %d published posts to inspect", len(rows))
    if not rows:
        return

    session_obj = None
    bot = get_bot()

    stats = {"ok": 0, "kb-only": 0, "noop": 0, "error": 0, "skip": 0}

    try:
        for pp, body in rows:
            channel = pp.channel_id or settings.telegram_channel_id
            mid_raw = pp.telegram_message_id
            try:
                mid = int(mid_raw) if mid_raw is not None else None
            except (TypeError, ValueError):
                mid = None
            if not mid:
                stats["skip"] += 1
                continue
            result = await update_one(bot, channel, mid, body or "")
            if result.startswith("error"):
                stats["error"] += 1
                log.warning("post %s msg %s — %s", pp.post_id, mid, result)
            else:
                stats[result] = stats.get(result, 0) + 1
                log.info("post %s msg %s — %s", pp.post_id, mid, result)
            # Telegram global limit: 30 edits / second. Be polite.
            await asyncio.sleep(0.4)
    finally:
        await close_bot()

    log.info("Done. Stats: %s", stats)


if __name__ == "__main__":
    asyncio.run(main())
