"""Admin approval queue.

When ``settings.require_admin_approval`` is True (default during the
calibration period), every generated post is parked in status
``pending_approval`` and sent to the admin's DM with the bot. The DM
contains the photo, the rendered caption, and two inline buttons:

    ✅ Опубликовать          ❌ Отклонить

When the admin clicks Опубликовать, the post is sent to the channel
exactly as previewed and the row goes to ``status='published'``. On
Отклонить the row is marked ``status='rejected'`` and never published.

The buttons are wired by ``register_approval_handlers(dispatcher)`` in
``app/bot/admin_commands.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    URLInputFile,
)
from sqlalchemy import select

from app.bot.telegram import get_bot
from app.config import settings
from app.db.models import Post as PostORM, PublishedPost as PublishedPostORM
from app.db.session import get_session
from app.services.publisher import (
    TelegramPublisher,
    _truncate_caption,    # type: ignore
)

logger = logging.getLogger(__name__)


def _approval_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Опубликовать",
                callback_data=f"approve:{post_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject:{post_id}",
            ),
        ]]
    )


def _resolve_photo(image_path_or_url: str):
    if image_path_or_url.startswith(("http://", "https://")):
        return URLInputFile(image_path_or_url)
    path = Path(image_path_or_url)
    if path.exists():
        return FSInputFile(str(path))
    raise FileNotFoundError(f"Image not found: {image_path_or_url}")


async def send_for_approval(post_id: int) -> bool:
    """Send a pending post to the admin's DM with approve/reject buttons.

    Returns True if the preview was delivered.
    """
    if not settings.telegram_admin_user_id:
        logger.warning(
            "TELEGRAM_ADMIN_USER_ID is not set — approval flow disabled, "
            "post %s will stay in pending_approval state",
            post_id,
        )
        return False

    async with get_session() as session:
        result = await session.execute(
            select(PostORM).where(PostORM.id == post_id)
        )
        post = result.scalar_one_or_none()
        if post is None:
            logger.error("send_for_approval: post %s not found", post_id)
            return False
        body = post.body or ""
        image = post.image_url or ""
        score = post.score or 0

    if not image:
        logger.warning("Post %s has no image — cannot send for approval", post_id)
        return False

    bot = get_bot()
    keyboard = _approval_keyboard(post_id)
    caption = _truncate_caption(
        f"<b>На модерацию</b> · score {score} · post #{post_id}\n\n{body}"
    )
    try:
        photo = _resolve_photo(image)
    except FileNotFoundError as e:
        logger.error("send_for_approval: %s", e)
        return False

    try:
        await bot.send_photo(
            chat_id=settings.telegram_admin_user_id,
            photo=photo,
            caption=caption,
            reply_markup=keyboard,
        )
    except TelegramAPIError as e:
        # The most common cause is the admin never having sent /start
        # to the bot. Telegram only allows DMs to users who initiated
        # the conversation first.
        logger.error(
            "Failed to DM admin (%s). Make sure you sent /start to the "
            "bot first. TELEGRAM_ADMIN_USER_ID=%s",
            e, settings.telegram_admin_user_id,
        )
        return False

    logger.info("Post %s sent to admin for approval", post_id)
    return True


async def approve_post(post_id: int) -> str | None:
    """Publish an approved post to the channel.

    Returns the Telegram message_id on success, None on failure.
    Auto-detects polls (image_prompt == "poll") and routes them to the
    poll-publishing path.
    """
    async with get_session() as session:
        result = await session.execute(
            select(PostORM).where(PostORM.id == post_id)
        )
        post = result.scalar_one_or_none()
        if post is None:
            logger.error("approve_post: post %s not found", post_id)
            return None
        if post.status not in ("pending_approval", "draft"):
            logger.warning(
                "approve_post: post %s is in state %s, refusing",
                post_id, post.status,
            )
            return None
        is_poll = post.image_prompt == "poll"
        body = post.body or ""
        image = post.image_url or ""

    # Polls take a different publishing path — no photo, just send_poll.
    if is_poll:
        from app.services.polls import approve_poll
        message_id = await approve_poll(post_id)
        return str(message_id) if message_id is not None else None

    publisher = TelegramPublisher()
    message_id = await publisher.publish_post(
        body=body,
        image_path_or_url=image,
        post_id=post_id,
    )
    if not message_id:
        return None

    async with get_session() as session:
        result = await session.execute(
            select(PostORM).where(PostORM.id == post_id)
        )
        post = result.scalar_one_or_none()
        if post is not None:
            post.status = "published"
            await session.flush()

    return message_id


async def reject_post(post_id: int) -> bool:
    """Mark a post as rejected so it never gets published."""
    async with get_session() as session:
        result = await session.execute(
            select(PostORM).where(PostORM.id == post_id)
        )
        post = result.scalar_one_or_none()
        if post is None:
            return False
        post.status = "rejected"
        await session.flush()
    logger.info("Post %s rejected", post_id)
    return True
