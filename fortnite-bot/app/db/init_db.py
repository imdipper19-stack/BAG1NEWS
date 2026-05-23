import logging

from app.db.models import Base
from app.db.session import engine

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Create all database tables if they don't exist."""
    logger.info("Initializing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized successfully.")
