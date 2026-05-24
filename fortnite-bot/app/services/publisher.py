"""Telegram publishing service.

Sends photo + caption to the configured channel, then logs to the
published_posts table.
"""

import logging
from pathlib import Path
from typing import Optional

from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    FSInputFile,
    URLInputFile,
)

from app.bot.telegram import get_bot
from app.config import settings
from app.db.models import PublishedPost
from app.db.session import get_session

logger = logging.getLogger(__name__)


def _truncate_caption(text: str, limit: int = 1024) -> str:
    """Telegram caption limit is 1024 chars; truncate gracefully."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _resolve_photo(image_path_or_url: str):
    """Return the proper aiogram input type for the given path or URL."""
    if image_path_or_url.startswith(("http://", "https://")):
        return URLInputFile(image_path_or_url)
    path = Path(image_path_or_url)
    if path.exists():
        return FSInputFile(str(path))
    raise FileNotFoundError(f"Image not found: {image_path_or_url}")


class TelegramPublisher:
    """Publishes posts to the configured Telegram channel."""

    def __init__(self, channel_id: Optional[str] = None):
        self.channel_id = channel_id or settings.telegram_channel_id
        self.bot = get_bot()

    async def publish_post(
        self,
        body: str,
        image_path_or_url: str,
        post_id: Optional[int] = None,
    ) -> Optional[str]:
        """Send a photo + caption.

        Returns the Telegram message_id (as string) on success, None on failure.
        Logs the publish event to the published_posts table when post_id is given.
        """
        caption = _truncate_caption(body)

        try:
            photo = _resolve_photo(image_path_or_url)
        except FileNotFoundError as e:
            logger.error("Cannot resolve image: %s", e)
            return None

        try:
            message = await self.bot.send_photo(
                chat_id=self.channel_id,
                photo=photo,
                caption=caption,
            )
        except TelegramAPIError as e:
            logger.error("Telegram API error publishing to %s: %s", self.channel_id, e)
            return None
        except Exception as e:
            logger.error("Unexpected error publishing to %s: %s", self.channel_id, e)
            return None

        message_id = str(message.message_id)
        logger.info("Published message %s to %s", message_id, self.channel_id)

        if post_id is not None:
            await self._log_published(post_id=post_id, message_id=message_id)

        return message_id

    async def publish_text(self, text: str, post_id: Optional[int] = None) -> Optional[str]:
        """Send a plain text message (fallback when no image)."""
        try:
            message = await self.bot.send_message(
                chat_id=self.channel_id,
                text=text[:4096],
                disable_web_page_preview=False,
            )
        except TelegramAPIError as e:
            logger.error("Telegram API error: %s", e)
            return None

        message_id = str(message.message_id)
        if post_id is not None:
            await self._log_published(post_id=post_id, message_id=message_id)
        return message_id

    async def _log_published(self, post_id: int, message_id: str) -> None:
        """Insert a row into published_posts."""
        try:
            async with get_session() as session:
                row = PublishedPost(
                    post_id=post_id,
                    telegram_message_id=message_id,
                    channel_id=str(self.channel_id),
                )
                session.add(row)
                await session.flush()
        except Exception as e:
            logger.error("Failed to log published_post for post_id=%s: %s", post_id, e)
