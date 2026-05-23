"""Admin command handlers for the Telegram bot.

Commands:
  /start    — bot greeting (sets your user as known to the bot)
  /status   — show bot health and today's post counter
  /stop     — emergency stop (Redis flag blocks publishing)
  /resume   — clear emergency stop
  /pending  — list all posts awaiting approval
  /myid     — print your Telegram user ID (so you can put it in .env)

Inline callbacks:
  approve:<post_id> / reject:<post_id> — buttons attached to approval DMs.
"""

import logging
from datetime import datetime, timezone

import redis.asyncio as redis
from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db.models import Post as PostORM
from app.db.session import get_session

logger = logging.getLogger(__name__)
EMERGENCY_KEY = "fortnite_bot:emergency_stop"
DAILY_POST_KEY_PREFIX = "fortnite_bot:posts_today"


def _redis_client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def _is_admin(message_or_cb) -> bool:
    """Restrict commands to the configured admin user (when set).

    Falls back to "any private chat" if TELEGRAM_ADMIN_USER_ID is not
    configured — useful during initial setup.
    """
    if hasattr(message_or_cb, "from_user"):
        user_id = message_or_cb.from_user.id if message_or_cb.from_user else 0
    else:
        user_id = 0

    if settings.telegram_admin_user_id:
        return user_id == settings.telegram_admin_user_id

    if hasattr(message_or_cb, "chat"):
        return message_or_cb.chat.type == "private"
    return True


# ---------------------------------------------------------------------------
# Plain commands
# ---------------------------------------------------------------------------


async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(
        f"🎮 Fortnite News Bot готов к работе.\n\n"
        f"Канал публикации: {settings.telegram_channel_id}\n"
        f"Твой Telegram ID: <code>{user_id}</code>\n\n"
        "Команды: /status /pending /stop /resume /myid"
    )


async def cmd_myid(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await message.answer(
        f"Твой Telegram user ID: <code>{user_id}</code>\n\n"
        "Добавь его в <code>.env</code> как <b>TELEGRAM_ADMIN_USER_ID</b> "
        "и пересобери контейнеры — бот будет присылать тебе посты на "
        "одобрение в этот чат."
    )


async def cmd_status(message: Message) -> None:
    if not await _is_admin(message):
        return
    r = _redis_client()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = await r.get(f"{DAILY_POST_KEY_PREFIX}:{today}") or "0"
        emergency = await r.get(EMERGENCY_KEY)
    finally:
        await r.aclose()

    async with get_session() as session:
        result = await session.execute(
            select(PostORM).where(PostORM.status == "pending_approval")
        )
        pending = len(list(result.scalars()))

    text = (
        "📊 <b>Статус бота</b>\n\n"
        f"Постов сегодня: {count} / {settings.max_posts_per_day}\n"
        f"На модерации: {pending}\n"
        f"Emergency-stop: {'🔴 ВКЛ' if emergency else '🟢 выкл'}\n"
        f"Канал: {settings.telegram_channel_id}\n"
        f"LLM модель: {settings.llm_model}\n"
        f"Approval mode: "
        f"{'🔴 включён' if settings.require_admin_approval else '🟢 авто-публикация'}"
    )
    await message.answer(text)


async def cmd_stop(message: Message) -> None:
    if not await _is_admin(message):
        return
    r = _redis_client()
    try:
        await r.set(EMERGENCY_KEY, "1")
        await message.answer("🛑 Аварийная остановка включена. Публикация постов приостановлена.")
    finally:
        await r.aclose()


async def cmd_resume(message: Message) -> None:
    if not await _is_admin(message):
        return
    r = _redis_client()
    try:
        await r.delete(EMERGENCY_KEY)
        await message.answer("▶️ Публикация возобновлена.")
    finally:
        await r.aclose()


async def cmd_pending(message: Message) -> None:
    if not await _is_admin(message):
        return
    async with get_session() as session:
        result = await session.execute(
            select(PostORM)
            .where(PostORM.status == "pending_approval")
            .order_by(PostORM.id.desc())
            .limit(10)
        )
        posts: list[PostORM] = list(result.scalars())

    if not posts:
        await message.answer("Очередь модерации пуста ✨")
        return

    lines = [f"<b>На модерации ({len(posts)}):</b>\n"]
    for p in posts:
        title = (p.title or "(без заголовка)")[:60]
        lines.append(f"• #{p.id} · score {p.score} · {title}")
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# Approval callbacks
# ---------------------------------------------------------------------------


async def cb_approve(query: CallbackQuery) -> None:
    if not await _is_admin(query):
        await query.answer("Нет доступа", show_alert=True)
        return

    data = query.data or ""
    try:
        post_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Неверный callback", show_alert=True)
        return

    # Local import to avoid circular dependency at module load time
    from app.services.approval import approve_post

    await query.answer("Публикую…")
    message_id = await approve_post(post_id)
    if message_id:
        await query.message.edit_caption(
            (query.message.caption or "")
            + f"\n\n✅ <b>Опубликовано</b> · message_id={message_id}",
        )
    else:
        await query.message.edit_caption(
            (query.message.caption or "") + "\n\n⚠️ Не удалось опубликовать"
        )


async def cb_reject(query: CallbackQuery) -> None:
    if not await _is_admin(query):
        await query.answer("Нет доступа", show_alert=True)
        return

    data = query.data or ""
    try:
        post_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Неверный callback", show_alert=True)
        return

    from app.services.approval import reject_post

    ok = await reject_post(post_id)
    if ok:
        await query.answer("Отклонено")
        await query.message.edit_caption(
            (query.message.caption or "") + "\n\n❌ <b>Отклонено</b>"
        )
    else:
        await query.answer("Пост не найден", show_alert=True)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_admin_handlers(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_myid, Command("myid"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_stop, Command("stop"))
    dp.message.register(cmd_resume, Command("resume"))
    dp.message.register(cmd_pending, Command("pending"))

    # Approval callbacks
    dp.callback_query.register(
        cb_approve, lambda q: (q.data or "").startswith("approve:")
    )
    dp.callback_query.register(
        cb_reject, lambda q: (q.data or "").startswith("reject:")
    )


async def is_emergency_stopped() -> bool:
    r = _redis_client()
    try:
        return bool(await r.get(EMERGENCY_KEY))
    finally:
        await r.aclose()
