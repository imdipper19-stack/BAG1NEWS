"""End-to-end test of the admin approval flow.

Creates a synthetic high-score post, generates an image, parks it as
pending_approval in the DB, and DMs the admin with the approve/reject
buttons.

Usage:
    docker-compose exec app python scripts/test_approval.py
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Post as PostORM  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.schemas import RawItem  # noqa: E402
from app.services.approval import send_for_approval  # noqa: E402
from app.services.image_generator import ImageGenerator  # noqa: E402
from app.services.scoring import score_item, should_publish  # noqa: E402
from app.services.verifier import verify_item  # noqa: E402
from app.services.writer import write_post  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("approval-test")


async def main() -> None:
    item = RawItem(
        source="x.com/HYPEX",
        source_level=3,
        title="Утечка: новый сезон Fortnite может быть посвящён супергеройской теме",
        url="https://twitter.com/HYPEX/status/test-approval",
        content=(
            "По данным датамайнеров, в файлах игры обнаружено несколько "
            "новых скинов в супергеройской тематике, новые наборы и "
            "косметические предметы, связанные с новым сезоном."
        ),
        image_url="",
        category="skin_leak",
        published_at=datetime.now(timezone.utc),
        is_official=False,
        is_leak=True,
    )

    print("\n=== verify ===")
    verified = verify_item(item)
    print(f"is_leak={verified.is_leak} is_official={verified.is_official}")

    print("\n=== score ===")
    scores = score_item(verified)
    decision = should_publish(scores["total"])
    print(f"score={scores['total']} decision={decision}")

    print("\n=== LLM rewrite ===")
    body = await write_post(verified)
    print(body[:300])

    print("\n=== Image generation ===")
    img_gen = ImageGenerator()
    image_path = await img_gen.generate_news_banner(
        topic=f"Fortnite — {verified.title}",
        headline=verified.title,
        reference_image_url=verified.image_url or None,
    )
    print(f"Image: {image_path}")

    if not image_path:
        print("\nImage generation failed — cannot test approval flow.")
        return

    print("\n=== Persisting as pending_approval ===")
    async with get_session() as session:
        post = PostORM(
            raw_item_id=None,
            title=verified.title,
            body=body,
            image_prompt="approval test",
            image_url=image_path,
            score=scores["total"],
            status="pending_approval",
        )
        session.add(post)
        await session.flush()
        post_id = post.id
    print(f"Post #{post_id} saved")

    print("\n=== Sending to admin DM ===")
    sent = await send_for_approval(post_id)
    print(f"Delivered: {sent}")
    if sent:
        print("\nЗайди в личку с ботом — там будет пост с кнопками.")


if __name__ == "__main__":
    asyncio.run(main())
