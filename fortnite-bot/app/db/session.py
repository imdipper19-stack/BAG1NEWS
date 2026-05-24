"""Async SQLAlchemy session management.

Important note for Celery: asyncpg connections are bound to the event
loop they were created in. Celery starts a fresh ``asyncio.run(...)``
per task, so a connection cached by SQLAlchemy from a previous task
will fail with::

    RuntimeError: unable to perform operation on <TCPTransport closed=True>

To avoid this we use ``NullPool`` — every checkout opens a new asyncpg
connection in the *current* event loop and closes it on commit. The
overhead is small (Postgres is on the same Docker network), and it's
the only safe choice when the engine is shared between FastAPI and
Celery.
"""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    """Async generator for FastAPI dependency injection."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session():
    """Async context manager for use outside FastAPI (Celery tasks, scripts)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
