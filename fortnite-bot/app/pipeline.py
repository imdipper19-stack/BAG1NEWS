"""End-to-end pipeline: one source → published Telegram post.

Steps (per spec section 8):
  1. Collect raw data from a named source
  2. Normalize to RawItem
  3. Deduplicate (Redis + Postgres)
  4. Score (rule-based)
  5. Verify (set is_official / is_leak)
  6. Write Russian post via LLM
  7. Generate image via Replicate
  8. Publish to Telegram (skip if dry_run)
  9. Persist to DB
"""

import logging
from typing import Awaitable, Callable

from app.collectors import (
    fortnite_api,
    fortnite_gg,
    fortnite_news,
    leaks_x,
    reddit,
    youtube,
)
from app.db.models import Post as PostORM, RawItem as RawItemORM
from app.db.session import get_session
from app.schemas import RawItem
from app.services import normalizer
from app.services.dedup import DuplicateChecker
from app.services.image_generator import ImageGenerator
from app.services.publisher import TelegramPublisher
from app.services.scoring import score_item, should_publish
from app.services.verifier import verify_item
from app.services.writer import write_post

logger = logging.getLogger(__name__)

SOURCE_FETCHERS: dict[str, Callable[[], Awaitable[list[dict]]]] = {
    "fortnite_api_news": fortnite_api.fetch_news,
    "fortnite_api_shop": fortnite_api.fetch_shop,
    "fortnite_api_cosmetics": fortnite_api.fetch_new_cosmetics,
    "fortnite_news": fortnite_news.fetch_official_news,
    "youtube": youtube.fetch_youtube_videos,
    "leaks_x": leaks_x.fetch_leak_sources,
    "reddit": reddit.fetch_reddit_leaks,
    "fortnite_gg": fortnite_gg.fetch_fortnite_gg,
}


async def run_pipeline(source: str, *, dry_run: bool = False, max_publish: int = 3) -> dict:
    """Run a full collect→publish cycle for one source.

    Args:
        source: key into SOURCE_FETCHERS
        dry_run: if True, skip Telegram publish + DB writes for posts
        max_publish: cap on number of posts to publish in this run

    Returns a summary dict with counts and a sample of generated posts.
    """
    if source not in SOURCE_FETCHERS:
        raise ValueError(f"Unknown source '{source}'. Choose from: {list(SOURCE_FETCHERS)}")

    summary: dict = {
        "source": source,
        "dry_run": dry_run,
        "fetched": 0,
        "normalized": 0,
        "unique": 0,
        "scored_to_publish": 0,
        "published": 0,
        "samples": [],
    }

    # 1. Collect
    fetch_fn = SOURCE_FETCHERS[source]
    raw_dicts = await fetch_fn()
    summary["fetched"] = len(raw_dicts)
    if not raw_dicts:
        return summary

    # 2. Normalize
    normalized: list[RawItem] = []
    for raw in raw_dicts:
        item = normalizer.normalize(raw)
        if item is not None:
            normalized.append(item)
    summary["normalized"] = len(normalized)

    # 3. Dedup
    checker = DuplicateChecker()
    try:
        unique = await checker.filter_duplicates(normalized)
    finally:
        await checker.close()
    summary["unique"] = len(unique)

    if not unique:
        return summary

    # 4-5. Score + verify
    publish_candidates: list[tuple[RawItem, dict]] = []
    for item in unique:
        verified = verify_item(item)
        scores = score_item(verified)
        decision = should_publish(scores["total"])
        if decision in ("immediate", "conditional"):
            publish_candidates.append((verified, scores))

    summary["scored_to_publish"] = len(publish_candidates)
    if not publish_candidates:
        return summary

    # Sort by score desc, take top max_publish
    publish_candidates.sort(key=lambda t: t[1]["total"], reverse=True)
    publish_candidates = publish_candidates[:max_publish]

    # Save raw items (when not dry-running)
    if not dry_run:
        async with get_session() as session:
            for item in unique:
                row = RawItemORM(
                    title=item.title,
                    url=item.url,
                    content=item.content,
                    image_url=item.image_url,
                    category=item.category,
                    is_official=item.is_official,
                    is_leak=item.is_leak,
                    published_at=item.published_at,
                )
                session.add(row)
                try:
                    await session.flush()
                except Exception:
                    await session.rollback()

    # 6-9. Write, generate image, publish, persist post
    publisher = TelegramPublisher() if not dry_run else None
    image_gen = ImageGenerator()
    published_count = 0

    for verified, scores in publish_candidates:
        # 6. Write
        body = await write_post(verified)

        # 7. Image — always generate a Fortnite-style banner; pass the source
        # image (e.g. real skin icon from Fortnite-API) as a reference so the
        # actual character appears on the banner.
        image = await image_gen.generate_news_banner(
            topic=f"Fortnite — {verified.title}",
            headline=verified.title,
            reference_image_url=verified.image_url or None,
        )

        sample = {
            "title": verified.title,
            "score": scores["total"],
            "url": verified.url,
            "is_leak": verified.is_leak,
            "is_official": verified.is_official,
            "body_preview": (body or "")[:300],
            "image": image,
        }
        summary["samples"].append(sample)

        # 8. Publish
        message_id = None
        if not dry_run and image and publisher is not None:
            message_id = await publisher.publish_post(body=body, image_path_or_url=image)
            if message_id:
                published_count += 1

        # 9. Persist post (when not dry-running)
        if not dry_run:
            async with get_session() as session:
                post = PostORM(
                    title=verified.title,
                    body=body,
                    image_prompt="",
                    image_url=image if isinstance(image, str) else verified.image_url,
                    score=scores["total"],
                    status="published" if message_id else "draft",
                )
                session.add(post)
                await session.flush()

    summary["published"] = published_count
    return summary
