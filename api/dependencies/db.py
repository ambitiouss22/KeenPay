"""Database session dependencies.

Two entry points, and the difference matters:

``get_db``
    The legacy unpinned session. Kept because the v1 repositories in
    ``api/repositories/`` still use it and filter by ``merchant_id`` in
    application code. Against the post-Phase-1 schema it reads **zero rows**
    from tenant tables, because row-level security is fail-closed and nothing
    pinned a tenant. Use it only for tenant-free work.

``get_tenant_db``
    The one new code should use. Resolves the tenant from the authenticated
    principal, pins the session to it, and hands back a session on which the
    database itself is enforcing isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.rls import set_tenant
from database import get_db as _get_db
from database import get_session_factory
from dependencies.auth import get_current_principal
from services.auth import AuthenticatedPrincipal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Unpinned session. See the module docstring before reaching for this."""
    if get_settings().use_in_memory_store:
        yield None  # type: ignore[misc]
        return
    async for session in _get_db():
        yield session


# Slug to tenant id. The mapping is immutable in practice — a tenant's slug is
# its identity — so caching it avoids a lookup on every single request.
_TENANT_ID_BY_SLUG: dict[str, uuid.UUID] = {}


async def _resolve_tenant_id(session: AsyncSession, slug: str) -> uuid.UUID:
    cached = _TENANT_ID_BY_SLUG.get(slug)
    if cached is not None:
        return cached

    tenant_id = await session.scalar(
        text("SELECT id FROM tenants WHERE slug = :slug AND active"), {"slug": slug}
    )
    if tenant_id is None:
        # 403 rather than 404: the caller authenticated fine, but the merchant
        # their token names is not one we will serve. Saying "not found" would
        # also confirm which slugs exist.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "TENANT_NOT_FOUND",
                    "message": f"No active tenant for merchant {slug!r}",
                }
            },
        )

    resolved = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    _TENANT_ID_BY_SLUG[slug] = resolved
    return resolved


async def get_tenant_db(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> AsyncGenerator[AsyncSession, None]:
    """Session pinned to the caller's tenant for the life of the request.

    The tenant comes from the verified token, never from a header or body
    parameter — otherwise a caller could simply ask for someone else's data and
    the pin would obligingly grant it.

    The pin is transaction-local, so it cannot survive back into the pool and
    affect whichever request borrows the connection next.
    """
    if get_settings().use_in_memory_store:
        yield None  # type: ignore[misc]
        return

    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.begin()
            # Prefer the tenant_id claim: it is signed, so it needs no lookup.
            # Tokens issued before Phase 2 lack it, so fall back to resolving
            # the merchant slug — also from the token, so equally trusted.
            # Neither path consults a header; see TenantContextMiddleware.
            if principal.tenant_id:
                tenant_id = uuid.UUID(str(principal.tenant_id))
            else:
                tenant_id = await _resolve_tenant_id(session, principal.merchant_id)
            await set_tenant(session, tenant_id)
            request.state.tenant_id = tenant_id
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_tenant_cache() -> None:
    """Drop the slug cache. For tests, and after seeding a new tenant."""
    _TENANT_ID_BY_SLUG.clear()


#: Annotated alias for routes: ``db: TenantDb``
TenantDb = Annotated[AsyncSession, Depends(get_tenant_db)]

__all__ = ["TenantDb", "get_db", "get_tenant_db", "reset_tenant_cache"]
