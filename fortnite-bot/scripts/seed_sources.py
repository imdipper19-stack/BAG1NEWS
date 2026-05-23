"""Seed the `sources` table with the 13 sources from the spec.

Idempotent: skips inserts if a source with the same URL already exists.

Usage (inside the Docker container):
    docker-compose exec app python scripts/seed_sources.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.init_db import init_db  # noqa: E402
from app.db.models import Source  # noqa: E402
from app.db.session import get_session  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# (name, url, source_level, type)
SOURCES = [
    ("Fortnite News",            "https://www.fortnite.com/news",                          1, "official_news"),
    ("Fortnite Battle Pass",     "https://www.fortnite.com/battle-pass",                   1, "official_battle_pass"),
    ("Fortnite YouTube",         "https://www.youtube.com/@fortnite",                      1, "youtube"),
    ("Fortnite X/Twitter",       "https://x.com/FortniteGame",                             1, "official_social"),
    ("Fortnite-API",             "https://fortnite-api.com",                               2, "api"),
    ("FNBR.co",                  "https://fnbr.co",                                        2, "shop_history"),
    ("Fortnite.GG",              "https://fortnite.gg",                                    2, "leaks_db"),
    ("HYPEX",                    "https://x.com/HYPEX",                                    3, "leak_account"),
    ("ShiinaBR",                 "https://x.com/ShiinaBR",                                 3, "leak_account"),
    ("FireMonkey",               "https://x.com/FireMonkey",                               3, "leak_account"),
    ("r/FortniteLeaks",          "https://www.reddit.com/r/FortniteLeaks",                 3, "reddit_leaks"),
    ("Fortnite Tracker",         "https://fortnitetracker.com",                            4, "competitive"),
    ("YouTube content creators", "https://www.youtube.com/results?search_query=fortnite",  4, "trend"),
]


async def main() -> None:
    await init_db()
    inserted = 0
    skipped = 0
    async with get_session() as session:
        for name, url, level, kind in SOURCES:
            existing = await session.execute(select(Source).where(Source.url == url))
            if existing.scalar_one_or_none() is not None:
                logger.info("Skipping existing source: %s", name)
                skipped += 1
                continue
            session.add(
                Source(
                    name=name,
                    url=url,
                    source_level=level,
                    type=kind,
                    is_active=True,
                )
            )
            inserted += 1
    logger.info("Done. Inserted %d, skipped %d.", inserted, skipped)


if __name__ == "__main__":
    asyncio.run(main())
