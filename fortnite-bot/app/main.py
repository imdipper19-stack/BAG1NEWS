"""FastAPI application entry point.

Provides:
  - /health endpoint
  - DB initialization on startup
  - Telegram bot polling for admin commands (background task)
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.telegram import close_bot, get_bot, get_dispatcher
from app.config import settings
from app.db.init_db import init_db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_bot_task: asyncio.Task | None = None


async def _start_bot_polling() -> None:
    bot = get_bot()
    dp = get_dispatcher()
    try:
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Bot polling crashed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_task
    logger.info("Starting up Fortnite News Bot…")
    try:
        await init_db()
    except Exception as e:
        logger.error("init_db failed: %s", e)

    _bot_task = asyncio.create_task(_start_bot_polling())
    logger.info("Bot polling task started")

    try:
        yield
    finally:
        if _bot_task:
            _bot_task.cancel()
            try:
                await _bot_task
            except (asyncio.CancelledError, Exception):
                pass
        await close_bot()
        logger.info("Shutdown complete")


app = FastAPI(
    title="Fortnite AI News Bot",
    description="Telegram channel automation for Fortnite news",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "channel": settings.telegram_channel_id,
        "model": settings.llm_model,
    }
