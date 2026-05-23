"""Watermark service.

Adds a "@channel + Telegram glyph" badge to the bottom-right of each
generated banner so reposts on other channels stay attributable.

We deliberately draw the watermark with Pillow rather than asking the
image model to render it — text rendering of Cyrillic on top of a
photographic banner is unreliable across all current image models, and
the layout/position would drift between generations.

The watermark contains:
  * a small Telegram-blue rounded square with the white paper-plane glyph
  * the channel handle in a bold sans-serif font
  * a thin contrasting outline so it stays legible on any background
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# Font candidates installed in the runtime image (fonts-dejavu-core).
# We try each in order until one loads.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

# Telegram brand blue
_TG_BLUE = (35, 158, 217, 255)
_WHITE = (255, 255, 255, 255)
_SHADOW = (0, 0, 0, 180)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load the first available bundled bold font, falling back to default."""
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_telegram_glyph(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
) -> None:
    """Draw a Telegram-style paper-plane inside the given square box.

    box: (left, top, right, bottom) in image coordinates.
    """
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    radius = max(2, int(width * 0.22))

    # Rounded blue square
    draw.rounded_rectangle(box, radius=radius, fill=_TG_BLUE)

    # Paper plane — stylized triangle pointing right with a fold
    # Coordinates expressed as fractions of the box, then scaled.
    cx, cy = left + width / 2, top + height / 2
    # Outer triangle
    plane_pts = [
        (cx - width * 0.30, cy - height * 0.05),  # top-left wing
        (cx + width * 0.32, cy - height * 0.32),  # tip
        (cx + width * 0.05, cy + height * 0.32),  # bottom
        (cx - width * 0.05, cy + height * 0.05),  # fold corner
    ]
    draw.polygon(plane_pts, fill=_WHITE)

    # Inner fold to give the plane a 3D feel
    fold_pts = [
        (cx - width * 0.05, cy + height * 0.05),
        (cx + width * 0.05, cy - height * 0.05),
        (cx + width * 0.20, cy - height * 0.18),
    ]
    draw.polygon(fold_pts, fill=(220, 235, 245, 255))


def add_watermark(
    image_path: str,
    handle: str,
    *,
    out_suffix: str = "_wm",
    output_format: str = "JPEG",
) -> str | None:
    """Overlay a Telegram-style watermark on the bottom-right of the image.

    Saves alongside the original (e.g. ``news_abc.jpeg`` →
    ``news_abc_wm.jpeg``) and returns the new path. Returns None and logs
    a warning on any error — callers fall back to the original.
    """
    try:
        img = Image.open(image_path).convert("RGBA")
    except (FileNotFoundError, OSError) as e:
        logger.warning("Watermark: cannot open %s: %s", image_path, e)
        return None

    w, h = img.size

    # Layout — scale with image height
    badge_height = max(36, int(h * 0.075))
    pad = max(12, int(h * 0.018))
    glyph_size = badge_height
    text_size = int(badge_height * 0.55)

    font = _load_font(text_size)

    # Measure text
    tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    text_bbox = tmp_draw.textbbox((0, 0), handle, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    # Total badge dimensions
    inner_pad = int(badge_height * 0.22)
    badge_w = glyph_size + inner_pad + text_w + inner_pad * 2
    badge_h = max(glyph_size, text_h) + inner_pad * 2

    # Position in bottom-right with padding
    x0 = w - badge_w - pad
    y0 = h - badge_h - pad

    # Pill background with soft shadow
    shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_draw.rounded_rectangle(
        (x0 + 4, y0 + 4, x0 + badge_w + 4, y0 + badge_h + 4),
        radius=int(badge_h * 0.45),
        fill=(0, 0, 0, 130),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))

    badge_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    badge_draw = ImageDraw.Draw(badge_layer)
    badge_draw.rounded_rectangle(
        (x0, y0, x0 + badge_w, y0 + badge_h),
        radius=int(badge_h * 0.45),
        fill=(0, 0, 0, 165),
    )

    # Telegram glyph (centered vertically inside the badge)
    glyph_x = x0 + inner_pad
    glyph_y = y0 + (badge_h - glyph_size) // 2
    _draw_telegram_glyph(
        badge_draw,
        (glyph_x, glyph_y, glyph_x + glyph_size, glyph_y + glyph_size),
    )

    # Handle text — drawn with a subtle shadow for legibility
    text_x = glyph_x + glyph_size + inner_pad
    text_y = y0 + (badge_h - text_h) // 2 - text_bbox[1]
    badge_draw.text((text_x + 1, text_y + 1), handle, font=font, fill=_SHADOW)
    badge_draw.text((text_x, text_y), handle, font=font, fill=_WHITE)

    composed = Image.alpha_composite(img, shadow_layer)
    composed = Image.alpha_composite(composed, badge_layer)

    # Build output path
    p = Path(image_path)
    out_path = p.with_name(f"{p.stem}{out_suffix}{p.suffix}")

    try:
        if output_format.upper() == "JPEG":
            composed = composed.convert("RGB")
            composed.save(out_path, format="JPEG", quality=92, optimize=True)
        else:
            composed.save(out_path, format=output_format)
    except OSError as e:
        logger.warning("Watermark: cannot save %s: %s", out_path, e)
        return None

    return str(out_path)
