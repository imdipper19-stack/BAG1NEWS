"""End-to-end smoke test with a synthetic high-score item.

Bypasses external collectors and feeds a high-relevance leak straight into
the score → verify → write → image → publish chain.

Usage:
    docker-compose exec app python scripts/test_e2e.py            # dry-run
    docker-compose exec app python scripts/test_e2e.py --publish  # real publish
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import RawItem  # noqa: E402
from app.services.image_generator import ImageGenerator  # noqa: E402
from app.services.publisher import TelegramPublisher  # noqa: E402
from app.services.scoring import score_item, should_publish  # noqa: E402
from app.services.verifier import verify_item  # noqa: E402
from app.services.writer import write_post  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("e2e")


async def main() -> None:
    publish = "--publish" in sys.argv

    # Synthetic hot item that should score high
    item = RawItem(
        source="x.com/HYPEX",
        source_level=3,
        title="Утечка: новый скин Marvel в следующем сезоне Fortnite",
        url="https://twitter.com/HYPEX/status/test-e2e",
        content=(
            "По данным датамайнеров, в файлах игры обнаружен новый скин по "
            "коллаборации с Marvel. Возможно, появится в боевом пропуске "
            "следующего сезона. Также найдены несколько новых наборов и "
            "косметических предметов, связанных с этой темой."
        ),
        image_url="",
        category="skin_leak",
        published_at=datetime.now(timezone.utc),
        is_official=False,
        is_leak=True,
    )

    print("\n=== Step 1: verify ===")
    verified = verify_item(item)
    print(f"is_official={verified.is_official}, is_leak={verified.is_leak}")

    print("\n=== Step 2: score ===")
    scores = score_item(verified)
    decision = should_publish(scores["total"])
    print(f"score={scores['total']} decision={decision} breakdown={scores}")

    print("\n=== Step 3: LLM rewrite (this calls the configured OpenAI-compatible API) ===")
    body = await write_post(verified)
    print(f"Body length: {len(body)} chars")
    print("---")
    print(body)
    print("---")

    print("\n=== Step 4: Image generation (configured image API) ===")
    img_gen = ImageGenerator()
    image_path = await img_gen.generate_news_banner(
        topic=f"Fortnite — {verified.title}",
        headline=verified.title,
        reference_image_url=verified.image_url or None,
    )
    print(f"Image path: {image_path}")

    if not publish:
        print("\n[dry-run] Skipping publish. Run with --publish to actually post.")
        return

    if not image_path:
        print("\nNo image generated — cannot publish without image.")
        return

    print("\n=== Step 5: Publish to Telegram ===")
    publisher = TelegramPublisher()
    msg_id = await publisher.publish_post(body=body, image_path_or_url=image_path)
    print(f"Telegram message_id: {msg_id}")


if __name__ == "__main__":
    asyncio.run(main())
