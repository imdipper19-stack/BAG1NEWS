"""LLM Russian News Writer.

Uses wellflow.dev API (OpenAI-compatible) with gpt-5.5 model to:
- Score news items (LLM-based scoring complementing the rule-based scorer)
- Rewrite news in Russian official news style with appropriate templates
"""

import httpx
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional
from app.config import settings
from app.schemas import RawItem
from app.services.verifier import get_leak_disclaimer, get_official_label

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# ----- Templates (from spec section 6) -----
# Each template ends with HASHTAGS that we append based on category for
# Telegram search discoverability.

TEMPLATE_OFFICIAL_NEWS = """⚡️ <b>{headline}</b>

Epic Games опубликовала свежую информацию о Fortnite.

Главное:
{key_points}

Обновление связано с {topic} и станет доступно {timing}.

🛒 Магазин для игроков:
{shop_url}

#Fortnite #ФортнайтНовости"""

TEMPLATE_SKIN_LEAK = """🔥 <b>ГОРЯЧАЯ УТЕЧКА</b> | {headline}

По данным датамайнеров, в файлах игры обнаружен новый косметический предмет — {skin_name}.

Что известно:
{key_points}

{disclaimer}

🛒 Магазин для игроков:
{shop_url}

#FortniteLeaks #ФортнайтУтечки #Скины"""

TEMPLATE_ITEM_SHOP = """🛒 <b>{headline}</b>

Сегодня в магазине появились новые и вернувшиеся предметы.

Главное:
{key_points}

🛒 Магазин для игроков:
{shop_url}

#FortniteShop #МагазинФортнайт #Скины #VBucks"""

TEMPLATE_NEXT_SEASON = """🚨 <b>СРОЧНО</b> | {headline}

Согласно свежей информации, следующий сезон Fortnite может быть связан с {topic}.

Ожидается:
{key_points}

{disclaimer}

🛒 Магазин для игроков:
{shop_url}

#FortniteSeason #ФортнайтСезон #БоевойПропуск"""

TEMPLATE_FREE_REWARDS = """🎁 <b>БЕСПЛАТНО</b> | {headline}

Игрокам стала доступна новая возможность получить {reward_name}.

Что нужно сделать:
{key_points}

Не откладывайте: такие награды часто доступны ограниченное время.

🛒 Магазин для игроков:
{shop_url}

#FortniteFree #БесплатныеНаграды #Fortnite"""

TEMPLATES = {
    "official_news": TEMPLATE_OFFICIAL_NEWS,
    "skin_leak": TEMPLATE_SKIN_LEAK,
    "item_shop": TEMPLATE_ITEM_SHOP,
    "next_season": TEMPLATE_NEXT_SEASON,
    "free_rewards": TEMPLATE_FREE_REWARDS,
}


def select_template(item: RawItem) -> str:
    """Choose template based on item flags and category."""
    cat = (item.category or "").lower()
    if "shop" in cat or "item_shop" in cat:
        return "item_shop"
    if "season" in cat or "next_season" in cat:
        return "next_season"
    if "free" in cat or "reward" in cat:
        return "free_rewards"
    if item.is_leak:
        return "skin_leak"
    return "official_news"


class LLMClient:
    """OpenAI-compatible client for wellflow.dev."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.base_url = (base_url or settings.llm_api_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        timeout: float = 60,
        max_retries: int = 3,
    ) -> str:
        """Send a chat completion request, return assistant message content.

        Retries up to ``max_retries`` times on transient failures (5xx, 429,
        network errors). Permanent failures (4xx other than 429) return ""
        immediately to avoid burning quota.
        """
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: str = ""
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                last_error = f"HTTP {status}"
                # Retry on server errors and rate limits
                if status >= 500 or status == 429:
                    backoff = 2 ** (attempt - 1) + (attempt * 0.5)
                    logger.warning(
                        "LLM HTTP %s (attempt %d/%d) — retrying in %.1fs",
                        status, attempt, max_retries, backoff,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(backoff)
                        continue
                logger.error(
                    "LLM HTTP error: %s %s", status, e.response.text[:300]
                )
                return ""
            except httpx.RequestError as e:
                last_error = f"network: {e}"
                logger.warning(
                    "LLM network error (attempt %d/%d): %s",
                    attempt, max_retries, e,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                logger.error("LLM network error after retries: %s", e)
                return ""
            except Exception as e:
                logger.error("LLM unexpected error: %s", e)
                return ""

            # Success — extract content
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                logger.error("Unexpected LLM response shape: %s", e)
                return ""

        logger.error("LLM failed after %d retries (last: %s)", max_retries, last_error)
        return ""


def _load_prompt(name: str) -> str:
    """Load a prompt template from app/prompts/."""
    path = PROMPTS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to load prompt %s: %s", name, e)
        return ""


async def score_with_llm(item: RawItem) -> dict:
    """Use the LLM to score an item. Returns dict with score/category/etc.

    Falls back to defaults if LLM fails or returns invalid JSON.
    """
    prompt_template = _load_prompt("scoring_prompt.txt")
    if not prompt_template:
        return {
            "score": 0,
            "category": "",
            "reason": "no prompt",
            "publish": False,
            "is_leak": item.is_leak,
            "is_official": item.is_official,
        }

    raw_item_str = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, default=str)
    prompt = prompt_template.replace("{raw_item}", raw_item_str)

    client = LLMClient()
    response = await client.chat(
        messages=[
            {
                "role": "system",
                "content": "Ты — строгий редактор Telegram-канала о Fortnite. Возвращай только JSON, без других слов.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )

    # Try to parse JSON. The model might wrap it in ```json ... ```
    content = response.strip()
    if content.startswith("```"):
        # Strip markdown fences
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].lstrip()
    try:
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("not a dict")
        # Normalize fields
        return {
            "score": int(result.get("score", 0)),
            "category": str(result.get("category", "")),
            "reason": str(result.get("reason", "")),
            "publish": bool(result.get("publish", False)),
            "is_leak": bool(result.get("is_leak", item.is_leak)),
            "is_official": bool(result.get("is_official", item.is_official)),
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse LLM scoring JSON: %s; raw=%s", e, content[:200])
        return {
            "score": 0,
            "category": "",
            "reason": "parse error",
            "publish": False,
            "is_leak": item.is_leak,
            "is_official": item.is_official,
        }


async def write_post(item: RawItem, template_name: Optional[str] = None) -> str:
    """Use the LLM to rewrite a news item as a Russian Telegram post.

    The new prompt (rewrite_news_ru.txt) does all the structural work
    itself — picks one of four "voice" styles and writes the post end
    to end. We don't pass any rigid template anymore; that was what
    made every post look identical.

    Returns the rendered post body. Falls back to a minimal, clean
    template only when the LLM is unavailable.
    """
    rewrite_prompt = _load_prompt("rewrite_news_ru.txt")

    # Trim source_data — pass only what the LLM actually needs to write
    # the post. We deliberately drop the raw `source` field to prevent
    # the model leaking the leaker's handle into the post.
    payload = {
        "title": item.title,
        "content": (item.content or "")[:600],
        "category": item.category,
        "is_leak": item.is_leak,
        "is_official": item.is_official,
    }
    source_data = json.dumps(payload, ensure_ascii=False, default=str)

    user_msg = (
        rewrite_prompt
        .replace("{shop_url}", settings.shop_url)
        .replace("{source_data}", source_data)
    )

    client = LLMClient()
    response = await client.chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты главный редактор русскоязычного Telegram-канала о "
                    "Fortnite и сам выступаешь первоисточником. "
                    "Никогда не упоминай датамайнеров или их ники. "
                    "Каждый пост звучит по-своему — варьируй стиль, "
                    "длину и тон."
                ),
            },
            {"role": "user", "content": user_msg},
        ],
        temperature=0.85,  # больше разнообразия в формулировках
        max_tokens=600,
    )

    if response and len(response.strip()) > 30:
        return response.strip()

    # Fallback — минимальный, чистый, без шаблона
    logger.warning("LLM returned empty/short response for %s, using fallback", item.title)
    return _render_fallback_simple(item)


def _render_fallback_simple(item: RawItem) -> str:
    """Minimal manual post when LLM is unavailable. No source attribution."""
    headline = (item.title or "Новость Fortnite")[:120]
    body = (item.content or "").strip()[:240]
    disclaimer = (
        "\n\nEpic Games пока не комментирует эту информацию официально."
        if item.is_leak else ""
    )
    return (
        f"⚡️ <b>{headline}</b>\n\n"
        f"{body}"
        f"{disclaimer}\n\n"
        f"🛒 Магазин для игроков: {settings.shop_url}\n"
        f"#Fortnite"
    )


def _render_fallback(item: RawItem, template: str) -> str:
    """Render a basic post if the LLM fails.

    Cleans up the source title before substitution so the brand prefix
    in the template (🔥 ГОРЯЧАЯ УТЕЧКА | …) doesn't end up duplicated
    when the source title already contains "Утечка:" / "Leak:" / similar.
    """
    raw = (item.title or "").strip()
    # Strip leading "Утечка:", "Leak:", "Новость:" etc. (any single
    # leading word followed by a colon)
    import re as _re
    cleaned = _re.sub(r"^[^:]{1,32}:\s*", "", raw, count=1)
    headline = cleaned[:120] if cleaned else raw[:120]

    key_points = "— " + (item.content[:200].replace("\n", " ").strip() or "Подробности скоро")
    disclaimer = get_leak_disclaimer() if item.is_leak else ""
    return (
        template
        .replace("{headline}", headline)
        .replace("{key_points}", key_points)
        .replace("{topic}", item.category or "обновлением")
        .replace("{timing}", "скоро")
        .replace("{skin_name}", headline)
        .replace("{reward_name}", headline)
        .replace("{disclaimer}", disclaimer)
        .replace("{shop_url}", settings.shop_url)
    )
