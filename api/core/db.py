"""Engine and session factory.

Thin layer over ``database.py`` rather than a second engine. Two engines would
mean two connection pools against the same database, each sized independently,
which is how a service quietly exhausts ``max_connections`` under load. The
engine lives in one place; this module adds the tenant-aware entry points.
"""

from __future__ import annotations

from database import check_db, get_engine, get_session_factory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def engine() -> AsyncEngine:
    """The single application engine."""
    return get_engine()


def session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the single application engine."""
    return get_session_factory()


__all__ = ["AsyncSession", "check_db", "engine", "session_factory"]
