"""Admin command handlers for the Telegram bot.

Commands:
  /start    — bot greeting (sets your user as known to the bot)
  /status   — show bot health and today's post counter
  /stop     — emergency stop (Redis flag blocks publishing)
  /resume   — clear emergency stop
  /pending  — list all posts awaiting approval
  /myid     — print your Telegram user ID (so you can put it in .env)
  /limit N  — set the daily post cap (1..100)
  /score N  — set the minimum publish score threshold (0..100)
  /reset    — drop runtime overrides; bot reverts to .env defaults

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
        "<b>Команды:</b>\n"
        "/status — текущий статус и параметры\n"
        "/pending — посты на модерации\n"
        "/limit N — лимит постов в день (1..100)\n"
        "/score N — минимальный score для публикации (0..100)\n"
        "/reset — сбросить настройки к значениям из .env\n"
        "/stop — экстренная остановка\n"
        "/resume — снять стоп\n"
        "/myid — твой Telegram ID"
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

    from app.services.runtime_settings import (
        get_max_posts_per_day,
        get_min_score_to_publish,
    )
    max_posts = await get_max_posts_per_day()
    min_score = await get_min_score_to_publish()

    text = (
        "📊 <b>Статус бота</b>\n\n"
        f"Постов сегодня: {count} / {max_posts}\n"
        f"На модерации: {pending}\n"
        f"Emergency-stop: {'🔴 ВКЛ' if emergency else '🟢 выкл'}\n"
        f"Канал: {settings.telegram_channel_id}\n"
        f"LLM модель: {settings.llm_model}\n"
        f"Approval mode: "
        f"{'🔴 включён' if settings.require_admin_approval else '🟢 авто-публикация'}\n\n"
        f"⚙️ <b>Параметры публикации</b>\n"
        f"Лимит постов: {max_posts} (изменить: /limit N)\n"
        f"Мин. score: {min_score} (изменить: /score N)"
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
# Runtime config commands
# ---------------------------------------------------------------------------


def _parse_int_arg(text: str) -> int | None:
    """Extract the first integer argument from `/cmd 42` or `/cmd@bot 42`."""
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


async def cmd_limit(message: Message) -> None:
    if not await _is_admin(message):
        return
    from app.services.runtime_settings import (
        get_max_posts_per_day,
        set_max_posts_per_day,
    )
    value = _parse_int_arg(message.text or "")
    if value is None:
        current = await get_max_posts_per_day()
        await message.answer(
            f"Текущий лимит постов в день: <b>{current}</b>\n\n"
            "Поменять: <code>/limit N</code> (например <code>/limit 5</code>)\n"
            "Допустимый диапазон: 1..100"
        )
        return
    if value < 1 or value > 100:
        await message.answer("⚠️ Значение должно быть от 1 до 100.")
        return
    new_value = await set_max_posts_per_day(value)
    await message.answer(
        f"✅ Лимит постов в день изменён: <b>{new_value}</b>\n"
        "Изменения применяются сразу, перезапуск не нужен."
    )


async def cmd_score(message: Message) -> None:
    if not await _is_admin(message):
        return
    from app.services.runtime_settings import (
        get_min_score_to_publish,
        set_min_score_to_publish,
    )
    value = _parse_int_arg(message.text or "")
    if value is None:
        current = await get_min_score_to_publish()
        await message.answer(
            f"Текущий минимальный score: <b>{current}</b>\n\n"
            "Поменять: <code>/score N</code> (например <code>/score 80</code>)\n"
            "Допустимый диапазон: 0..100\n\n"
            "Шкала:\n"
            "• 50 — почти всё\n"
            "• 70 — сбалансировано (по умолчанию)\n"
            "• 80 — только серьёзный контент\n"
            "• 85 — только сенсации"
        )
        return
    if value < 0 or value > 100:
        await message.answer("⚠️ Значение должно быть от 0 до 100.")
        return
    new_value = await set_min_score_to_publish(value)
    await message.answer(
        f"✅ Минимальный score изменён: <b>{new_value}</b>\n"
        "Изменения применяются сразу."
    )


async def cmd_reset(message: Message) -> None:
    if not await _is_admin(message):
        return
    from app.services.runtime_settings import reset_to_env
    await reset_to_env()
    await message.answer(
        "♻️ Параметры сброшены к значениям из <code>.env</code>:\n"
        f"• Лимит постов: {settings.max_posts_per_day}\n"
        f"• Мин. score: {settings.min_score_to_publish}"
    )


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
    dp.message.register(cmd_limit, Command("limit"))
    dp.message.register(cmd_score, Command("score"))
    dp.message.register(cmd_reset, Command("reset"))

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
