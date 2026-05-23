"""Telegram bot setup using aiogram 3.x.

If TELEGRAM_PROXY_URL is set in .env, all Telegram traffic goes through
that HTTP/HTTPS proxy — useful when the VPS is in a region where the
Bot API is blocked at the network level (e.g. Russia, Iran).
"""

import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None


def _build_session() -> AiohttpSession | None:
    """Return an AiohttpSession with proxy if configured, else None.

    aiogram falls back to a default session when given None.
    """
    proxy = getattr(settings, "telegram_proxy_url", "") or ""
    if not proxy:
        return None
    logger.info("Using Telegram proxy: %s", proxy.split("@")[-1])
    return AiohttpSession(proxy=proxy)


def get_bot() -> Bot:
    """Return a singleton Bot instance."""
    global _bot
    if _bot is None:
        session = _build_session()
        kwargs: dict = {
            "token": settings.telegram_bot_token,
            "default": DefaultBotProperties(parse_mode=ParseMode.HTML),
        }
        if session is not None:
            kwargs["session"] = session
        _bot = Bot(**kwargs)
    return _bot


def get_dispatcher() -> Dispatcher:
    """Return a singleton Dispatcher instance with admin handlers attached."""
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        from app.bot.admin_commands import register_admin_handlers
        register_admin_handlers(_dp)
    return _dp


async def close_bot() -> None:
    """Close the bot session — call on shutdown."""
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None
