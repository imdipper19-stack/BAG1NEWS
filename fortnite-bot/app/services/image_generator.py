"""Image generation service using Google Nano Banana models on Replicate.

Why these models:
- Nano Banana 2 is currently #1 on the text-to-image leaderboard for
  character consistency and accurate multilingual (Russian) text rendering.
- It has built-in `image_search: true` that pulls real reference images
  from Google when generating, so it can render real Fortnite skin
  styles without us having to ship the icons ourselves.
- It accepts up to 14 reference images via `image_input`, so when we
  already have a skin icon URL from Fortnite-API we can pass it directly.
- Unlike OpenAI gpt-image-2 it does not aggressively block branded
  prompts ("Fortnite", "Marvel", "Star Wars"), which is exactly what we
  need for this channel.

Strategy:
1. Try `google/nano-banana-2` first (fast, ~2.5c per image).
2. On failure, fall back to `google/nano-banana-pro` (premium quality).
3. If both fail, return None and the post is skipped.
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import httpx
import replicate

from app.config import settings

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "google/nano-banana-2"
FALLBACK_MODEL = "google/nano-banana-pro"
LAST_RESORT_MODEL = "bytedance/seedream-4"

# Third-party brands that Google's safety layer flags (E005). We keep them in
# the published Telegram post, but strip them from the image prompt so the
# banner can still be generated. Fortnite/Epic Games are NOT in this list —
# the models accept them.
_BRAND_REPLACEMENTS = {
    "marvel": "superhero",
    "dc comics": "superhero",
    "star wars": "sci-fi space",
    "disney": "fantasy",
    "pixar": "animated",
    "lego": "blocky toy",
    "anime": "stylized cartoon",
    "naruto": "ninja",
    "dragon ball": "martial arts hero",
    "pokemon": "creature",
    "ferrari": "race car",
    "lamborghini": "supercar",
    "nike": "sport",
    "adidas": "sport",
    "spiderman": "web hero",
    "spider-man": "web hero",
    "iron man": "armored hero",
    "batman": "dark knight",
    "superman": "flying hero",
    "deadpool": "red costumed hero",
    "wolverine": "clawed hero",
    "darth vader": "dark armored figure",
    "yoda": "wise small alien",
}


def _strip_third_party_brands(text: str) -> str:
    """Remove third-party IP names from prompts. Fortnite/Epic stay."""
    if not text:
        return ""
    out = text
    for needle, replacement in _BRAND_REPLACEMENTS.items():
        for variant in (needle, needle.capitalize(), needle.upper(), needle.title()):
            out = out.replace(variant, replacement)
    return out


IMAGES_DIR = Path(__file__).parent.parent.parent / "images"
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class ImageGenerator:
    """Generate Fortnite-themed news banners via Replicate."""

    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or settings.replicate_api_token
        os.environ["REPLICATE_API_TOKEN"] = self.api_token
        self.client = replicate.Client(api_token=self.api_token)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------- prompt building --------------------

    def _load_prompt_template(self) -> str:
        path = PROMPTS_DIR / "image_banner.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to load image_banner.txt: %s; using fallback", e)
            return (
                "Premium Fortnite news banner in the official Fortnite art "
                "style — vibrant, colorful, polished 3D render. "
                "Subject: {topic}. Bold readable Russian headline at the top: "
                '"{headline}". Cinematic lighting, dynamic composition, '
                "16:9 landscape."
            )

    def _build_prompt(
        self,
        topic: str,
        headline: str,
        style: str = "news",
        has_reference: bool = False,
    ) -> str:
        # Strip third-party brand names (Marvel, Star Wars, Disney, …) that
        # trip Google/OpenAI safety filters. "Fortnite" / "Epic Games" stay.
        # Both topic (scene description) and headline (text rendered on the
        # banner) are sanitized so the safety check passes; the published
        # Telegram caption remains untouched.
        clean_topic = _strip_third_party_brands(topic)
        clean_headline = _strip_third_party_brands(headline)
        template = self._load_prompt_template()
        prompt = (
            template
            .replace("{topic}", clean_topic)
            .replace("{headline}", clean_headline)
            .replace("{style}", style)
        )
        if has_reference:
            prompt += (
                "\n\nReference image is provided. Use it as the central "
                "character/subject of the banner. Preserve its exact "
                "appearance, colors, and design — do not invent a new "
                "character. Render the rest of the scene around it in "
                "the official Fortnite art style."
            )
        return prompt

    # -------------------- replicate calls --------------------

    async def _run_model(
        self,
        model: str,
        prompt: str,
        input_images: list[str] | None = None,
        use_image_search: bool = True,
    ) -> Optional[str]:
        """Run a Replicate model and return the first output URL.

        Returns None on flagged content / API error / empty output.
        Each model has slightly different input schema, so we branch.
        """
        input_data: dict = {"prompt": prompt}

        if model.startswith("google/nano-banana"):
            # Nano Banana 2 / Pro
            input_data["aspect_ratio"] = "16:9"
            input_data["resolution"] = "1K"
            input_data["output_format"] = "jpg"
            if input_images:
                input_data["image_input"] = input_images
            elif use_image_search:
                input_data["image_search"] = True
        elif model.startswith("bytedance/seedream"):
            # Seedream-4
            input_data["size"] = "2K"
            input_data["aspect_ratio"] = "16:9"
            input_data["enhance_prompt"] = True
            if input_images:
                input_data["image_input"] = input_images
        else:
            input_data["aspect_ratio"] = "16:9"

        def _run() -> Optional[str]:
            try:
                output = self.client.run(model, input=input_data)
            except Exception as e:
                logger.warning("Replicate model %s failed: %s", model, e)
                return None

            item = output[0] if isinstance(output, list) and output else output
            if item is None:
                return None
            if hasattr(item, "url"):
                url_attr = getattr(item, "url")
                url = url_attr() if callable(url_attr) else url_attr
                return str(url) if url else None
            return str(item)

        return await asyncio.to_thread(_run)

    async def _download_image(self, url: str, prefix: str = "banner") -> Optional[str]:
        """Download remote image, watermark it, save to images/ directory."""
        if not url:
            return None
        # Detect extension from URL or default to jpg
        ext = "jpg"
        for candidate in (".webp", ".png", ".jpg", ".jpeg"):
            if candidate in url.lower():
                ext = candidate.lstrip(".")
                break
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}.{ext}"
        path = IMAGES_DIR / filename
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(url)
                response.raise_for_status()
                path.write_bytes(response.content)
        except (httpx.HTTPError, OSError) as e:
            logger.error("Failed to download image from %s: %s", url, e)
            return None

        # Stamp the channel watermark in the bottom-right. Runs in a
        # thread because Pillow is sync/CPU-bound.
        try:
            from app.services.watermark import add_watermark
            from app.config import settings as _settings

            handle = _settings.telegram_channel_id or ""
            if handle:
                wm_path = await asyncio.to_thread(
                    add_watermark,
                    str(path),
                    handle,
                    out_suffix="_wm",
                    output_format="JPEG" if ext in ("jpg", "jpeg") else "PNG",
                )
                if wm_path:
                    return wm_path
        except Exception as e:
            logger.warning("Watermark step failed, returning unwatermarked: %s", e)

        return str(path)

    # -------------------- public API --------------------

    async def generate_news_banner(
        self,
        topic: str,
        headline: str,
        style: str = "news",
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        """Generate a banner. Returns local file path or None.

        If `reference_image_url` is given (e.g. a real skin icon from
        Fortnite-API), it is passed as `image_input` so the actual
        character ends up on the banner. Otherwise, `image_search` lets
        the model find Fortnite reference imagery itself.
        """
        prompt = self._build_prompt(
            topic=topic,
            headline=headline,
            style=style,
            has_reference=bool(reference_image_url),
        )

        input_images = [reference_image_url] if reference_image_url else None

        # Primary: nano-banana-2 (fast, best Russian text rendering)
        url = await self._run_model(
            PRIMARY_MODEL,
            prompt,
            input_images=input_images,
            use_image_search=not bool(input_images),
        )

        # Fallback 1: nano-banana-pro (premium quality)
        if not url:
            logger.info("Primary model failed, falling back to %s", FALLBACK_MODEL)
            url = await self._run_model(
                FALLBACK_MODEL,
                prompt,
                input_images=input_images,
                use_image_search=not bool(input_images),
            )

        # Fallback 2: seedream-4 (less strict safety filter)
        if not url:
            logger.info("Both Google models failed, falling back to %s", LAST_RESORT_MODEL)
            url = await self._run_model(
                LAST_RESORT_MODEL,
                prompt,
                input_images=input_images,
                use_image_search=False,  # seedream doesn't support image_search
            )

        if not url:
            logger.error("All image models failed for: %s", headline[:60])
            return None

        return await self._download_image(url, prefix="news")

    async def generate_shop_banner(
        self,
        items: list[str],
        reference_image_url: str | None = None,
    ) -> Optional[str]:
        items_str = ", ".join(items[:5]) if items else "новые скины"
        return await self.generate_news_banner(
            topic=f"Fortnite item shop update featuring {items_str}",
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
            topic=f"Fortnite leaked skin: {skin_name}",
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
