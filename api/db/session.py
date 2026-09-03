"""Tenant-pinned database sessions.

Every session handed out here has ``app.tenant_id`` bound before any caller
touches it, so row-level security is already in force by the time application
code runs a query. There is no code path that yields an unpinned session for
tenant data — that is deliberate, and it is what makes "we forgot to filter by
tenant" stop being a class of bug.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import session_factory
from core.rls import coerce_tenant_id, set_tenant


@asynccontextmanager
async def tenant_session(
    tenant_id: str | uuid.UUID,
) -> AsyncGenerator[AsyncSession, None]:
    """Open a session pinned to ``tenant_id`` for one transaction.

    Commits on clean exit, rolls back on exception. The tenant setting is
    transaction-local, so it is gone the moment the transaction ends and cannot
    leak to the next borrower of the pooled connection.

        async with tenant_session(principal.tenant_id) as session:
            orders = await OrderRepository(session).list_recent()
    """
    tid = coerce_tenant_id(tenant_id)
    factory = session_factory()

    async with factory() as session:
        try:
            # begin() first: set_config(..., local) needs a live transaction, and
            # outside one the setting is discarded immediately and every query
            # would come back empty.
            await session.begin()
            await set_tenant(session, tid)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def tenant_session_by_slug(slug: str) -> AsyncGenerator[AsyncSession, None]:
    """Same as :func:`tenant_session` but resolves a merchant slug first.

    Convenience for the v1 code paths that still carry ``merchant_id`` strings
    rather than tenant UUIDs. The lookup runs on an unpinned session, which is
    safe because ``tenants`` is deliberately not tenant-scoped.
    """
    factory = session_factory()
    async with factory() as lookup:
        tenant_id = await lookup.scalar(
            text("SELECT id FROM tenants WHERE slug = :slug AND active"), {"slug": slug}
        )

    if tenant_id is None:
        raise LookupError(f"No active tenant with slug {slug!r}")

    async with tenant_session(tenant_id) as session:
        yield session


async def resolve_tenant_id(session: AsyncSession, slug: str) -> uuid.UUID:
    """Map a merchant slug to its tenant UUID."""
    tenant_id = await session.scalar(
        text("SELECT id FROM tenants WHERE slug = :slug AND active"), {"slug": slug}
    )
    if tenant_id is None:
        raise LookupError(f"No active tenant with slug {slug!r}")
    return coerce_tenant_id(tenant_id)


__all__ = ["resolve_tenant_id", "tenant_session", "tenant_session_by_slug"]
