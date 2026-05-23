"""Telegram bot setup using aiogram 3.x."""

import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None

def get_bot() -> Bot:
    """Return a singleton Bot instance."""
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
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
