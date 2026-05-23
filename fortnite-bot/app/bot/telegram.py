"""Telegram bot setup using aiogram 3.x.

If TELEGRAM_PROXY_URL is set in .env, all Telegram traffic goes through
that HTTP/HTTPS proxy — useful when the VPS is in a region where the
Bot API is blocked at the network level (e.g. Russia, Iran).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.methods.base import TelegramMethod

from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dp: Dispatcher | None = None


class TimeoutAiohttpSession(AiohttpSession):
    """AiohttpSession that enforces a hard timeout on every Telegram call.

    Without this aiogram 3.7's getUpdates can hang forever on flaky
    HTTP proxies, blocking the bot from ever reaching the polling loop.
    """

    HARD_TIMEOUT_SEC = 30

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod,
        timeout: int | None = None,
    ) -> Any:
        if timeout is None or timeout <= 0:
            timeout = self.HARD_TIMEOUT_SEC
        return await super().make_request(bot, method, timeout=timeout)


def _build_session() -> AiohttpSession | None:
    proxy = getattr(settings, "telegram_proxy_url", "") or ""
    if not proxy:
        return TimeoutAiohttpSession()
    logger.info("Using Telegram proxy: %s", proxy.split("@")[-1])
    return TimeoutAiohttpSession(proxy=proxy)


def get_bot() -> Bot:
    """Return a singleton Bot instance."""
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=_build_session(),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        from app.bot.admin_commands import register_admin_handlers
        register_admin_handlers(_dp)
    return _dp


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None
