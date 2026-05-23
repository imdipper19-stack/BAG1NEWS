"""Celery application instance.

Uses Redis as both broker and result backend.
Beat schedule defined here drives all collection + processing cadences.
"""

from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "fortnite_bot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Beat schedule (UTC). Cadences from spec section 11.1.
celery_app.conf.beat_schedule = {
    "collect-fortnite-api": {
        "task": "app.tasks.collect_fortnite_api",
        "schedule": 30 * 60,  # every 30 minutes
    },
    "collect-fortnite-news": {
        "task": "app.tasks.collect_fortnite_news",
        "schedule": 30 * 60,
    },
    "collect-youtube": {
        "task": "app.tasks.collect_youtube",
        "schedule": 15 * 60,
    },
    "collect-leaks": {
        "task": "app.tasks.collect_leaks",
        "schedule": 10 * 60,
    },
    "collect-reddit": {
        "task": "app.tasks.collect_reddit",
        "schedule": 30 * 60,
    },
    "collect-fortnite-gg": {
        "task": "app.tasks.collect_fortnite_gg",
        "schedule": 60 * 60,
    },
    "collect-shop-daily": {
        "task": "app.tasks.collect_shop",
        "schedule": crontab(hour=0, minute=5),  # daily 00:05 UTC
    },
    "process-queue": {
        "task": "app.tasks.process_queue",
        "schedule": 5 * 60,  # every 5 minutes
    },
    "daily-shop-digest": {
        "task": "app.tasks.daily_shop_digest",
        # 30 minutes after the daily Fortnite shop reset (00:05 UTC)
        "schedule": crontab(hour=0, minute=35),
    },
    "weekly-leaks-digest": {
        "task": "app.tasks.weekly_leaks_digest",
        # Every Friday 18:00 UTC — captures the week's leaks before
        # weekend traffic peak
        "schedule": crontab(hour=18, minute=0, day_of_week="friday"),
    },
    # ---- themed weekly series + engagement polls ----
    "monday-shop-recap": {
        "task": "app.tasks.monday_shop_recap",
        "schedule": crontab(hour=15, minute=0, day_of_week="monday"),
    },
    "wednesday-official-roundup": {
        "task": "app.tasks.wednesday_official_roundup",
        "schedule": crontab(hour=15, minute=0, day_of_week="wednesday"),
    },
    "sunday-deals": {
        "task": "app.tasks.sunday_deals",
        "schedule": crontab(hour=15, minute=0, day_of_week="sunday"),
    },
    # Engagement poll every 3 days at 16:00 UTC. The task itself enforces
    # the 3-day cooldown if the worker schedule slips.
    "engagement-poll": {
        "task": "app.tasks.engagement_poll",
        "schedule": crontab(hour=16, minute=0, day_of_month="*/3"),
    },
}
