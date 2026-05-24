"""Image generation service via Closerouter OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).parent.parent.parent / "images"
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class ImageGenerator:
    """Generate Fortnite-themed Telegram news banners via Closerouter."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        image_size: str | None = None,
    ):
        self.api_key = api_key or settings.image_api_key or settings.llm_api_key
        self.base_url = (base_url or settings.image_api_url).rstrip("/")
        self.model = model or settings.image_model
        self.image_size = image_size or settings.image_size
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def _url(self, path: str) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/{path.lstrip('/')}"
        return f"{self.base_url}/v1/{path.lstrip('/')}"

    # -------------------- prompt building --------------------

    def _load_prompt_template(self) -> str:
        path = PROMPTS_DIR / "image_banner.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to load image_banner.txt: %s; using fallback", e)
            return (
                "Create a premium 16:9 Telegram news image in a polished "
                "Fortnite-inspired gaming style. Topic: {topic}. Headline "
                "context: {headline}. Minimal readable Russian text, clean "
                "composition, no official logos, no watermark."
            )

    def _build_prompt(
        self,
        topic: str,
        headline: str,
        style: str = "news",
        has_reference: bool = False,
    ) -> str:
        from app.services.title_cleaner import clean_title as _clean_attrib

        clean_topic = _clean_attrib(topic or "")
        clean_headline = _clean_attrib(headline or "")
        template = self._load_prompt_template()
        prompt = (
            template
            .replace("{topic}", clean_topic)
            .replace("{headline}", clean_headline)
            .replace("{style}", style)
        )
        if has_reference:
            prompt += (
                "\n\nA reference image URL exists for this news item. If the "
                "image model can use web context, treat it only as a loose "
                "visual reference for the central subject. Do not copy logos."
            )
        return prompt

    # -------------------- API calls --------------------

    async def _post_generation(self, prompt: str) -> dict[str, Any] | None:
        """Call the OpenAI-compatible image generation endpoint."""
        if not self.api_key:
            logger.error("Image API key is not configured")
            return None

        url = self._url("images/generations")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload_variants = [
            {
                "model": self.model,
                "prompt": prompt,
                "size": self.image_size,
                "quality": "auto",
                "n": 1,
            },
            {
                "model": self.model,
                "prompt": prompt,
                "size": self.image_size,
                "n": 1,
            },
            {
                "model": self.model,
                "prompt": prompt,
                "size": "auto",
                "n": 1,
            },
        ]

        last_error = ""
        async with httpx.AsyncClient(timeout=180) as client:
            for attempt, payload in enumerate(payload_variants, start=1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    last_error = f"HTTP {status}: {e.response.text[:500]}"
                    if status in (400, 422) and attempt < len(payload_variants):
                        logger.warning(
                            "Image payload variant %s rejected, retrying: %s",
                            attempt,
                            last_error,
                        )
                        continue
                    logger.error("Image generation HTTP error: %s", last_error)
                    return None
                except httpx.RequestError as e:
                    last_error = f"network: {e}"
                    logger.error("Image generation network error: %s", e)
                    return None
                except Exception as e:
                    last_error = str(e)
                    logger.error("Image generation unexpected error: %s", e)
                    return None

        logger.error("Image generation failed: %s", last_error)
        return None

    def _extract_image_payload(self, data: dict[str, Any]) -> tuple[str, str] | None:
        """Return (kind, value), where kind is url or b64."""
        items: list[Any] = []
        if isinstance(data.get("data"), list):
            items.extend(data["data"])
        if isinstance(data.get("output"), list):
            items.extend(data["output"])
        if data.get("url") or data.get("b64_json"):
            items.append(data)

        for item in items:
            if isinstance(item, str):
                if item.startswith(("http://", "https://")):
                    return ("url", item)
                if item.startswith("data:image"):
                    return ("b64", item.split(",", 1)[-1])
                return ("b64", item)
            if not isinstance(item, dict):
                continue
            for key in ("url", "image_url", "uri"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    if value.startswith("data:image"):
                        return ("b64", value.split(",", 1)[-1])
                    if value.startswith(("http://", "https://")):
                        return ("url", value)
            value = item.get("b64_json") or item.get("base64") or item.get("image")
            if isinstance(value, str) and value:
                if value.startswith("data:image"):
                    value = value.split(",", 1)[-1]
                return ("b64", value)
        return None

    async def _save_image_payload(
        self,
        kind: str,
        value: str,
        prefix: str = "news",
    ) -> Optional[str]:
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}"

        if kind == "b64":
            path = IMAGES_DIR / f"{filename}.png"
            try:
                path.write_bytes(base64.b64decode(value))
            except Exception as e:
                logger.error("Failed to decode generated image: %s", e)
                return None
            return await self._watermark(str(path))

        ext = "jpg"
        for candidate in (".webp", ".png", ".jpg", ".jpeg"):
            if candidate in value.lower():
                ext = candidate.lstrip(".")
                break
        path = IMAGES_DIR / f"{filename}.{ext}"
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.get(value)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "png" in content_type:
                    path = path.with_suffix(".png")
                elif "webp" in content_type:
                    path = path.with_suffix(".webp")
                elif "jpeg" in content_type or "jpg" in content_type:
                    path = path.with_suffix(".jpg")
                path.write_bytes(response.content)
        except (httpx.HTTPError, OSError) as e:
            logger.error("Failed to download generated image from %s: %s", value, e)
            return None

        return await self._watermark(str(path))

    async def _watermark(self, path: str) -> str:
        try:
            from app.services.watermark import add_watermark

            handle = settings.telegram_channel_id or ""
            if not handle:
                return path
            suffix = Path(path).suffix.lower()
            output_format = "JPEG" if suffix in (".jpg", ".jpeg") else "PNG"
            wm_path = await asyncio.to_thread(
                add_watermark,
                path,
                handle,
                out_suffix="_wm",
                output_format=output_format,
            )
            return wm_path or path
        except Exception as e:
            logger.warning("Watermark step failed, returning unwatermarked: %s", e)
            return path

    # -------------------- public API --------------------

    async def generate_news_banner(
        self,
        topic: str,
        headline: str,
        style: str = "news",
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        """Generate a 16:9 Telegram-ready banner and return a local file path."""
        prompt = self._build_prompt(
            topic=topic,
            headline=headline,
            style=style,
            has_reference=bool(reference_image_url),
        )
        data = await self._post_generation(prompt)
        if not data:
            return None

        payload = self._extract_image_payload(data)
        if not payload:
            logger.error("Unexpected image response shape: %s", str(data)[:500])
            return None

        kind, value = payload
        return await self._save_image_payload(kind, value, prefix="news")

    async def generate_shop_banner(
        self,
        items: list[str],
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        items_str = ", ".join(items[:5]) if items else "новые предметы магазина"
        return await self.generate_news_banner(
            topic=f"Fortnite item shop update: {items_str}",
            headline="Магазин Fortnite обновился",
            style="shop",
            reference_image_url=reference_image_url,
        )

    async def generate_leak_banner(
        self,
        skin_name: str,
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        return await self.generate_news_banner(
            topic=f"Fortnite leak: {skin_name}",
            headline=f"Утечка: {skin_name}",
            style="leak",
            reference_image_url=reference_image_url,
        )

    async def generate_season_banner(
        self,
        theme: str,
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        return await self.generate_news_banner(
            topic=f"Fortnite next season theme: {theme}",
            headline="Следующий сезон Fortnite",
            style="season",
            reference_image_url=reference_image_url,
        )
