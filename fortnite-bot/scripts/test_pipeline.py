"""Manual smoke test of the end-to-end pipeline (dry-run, no Telegram publish).

Usage (inside the Docker container):
    docker-compose exec app python scripts/test_pipeline.py [source]

Default source is "fortnite_api_news". Available sources:
    fortnite_api_news, fortnite_api_shop, fortnite_api_cosmetics,
    fortnite_news, youtube, leaks_x, reddit, fortnite_gg
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_pipeline  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else "fortnite_api_news"
    print(f"\n=== Running pipeline test (source={source}, dry_run=True) ===\n")
    summary = await run_pipeline(source=source, dry_run=True, max_publish=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
