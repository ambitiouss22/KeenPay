"""Async SQLAlchemy session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database import get_db as _get_db


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if get_settings().use_in_memory_store:
        yield None  # type: ignore[misc]
        return
    async for session in _get_db():
        yield session
