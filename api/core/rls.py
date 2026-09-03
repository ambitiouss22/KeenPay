"""Row-level security helpers.

The database enforces tenant isolation through a policy on every tenant-owned
table::

    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)

This module is the only place that setting is written, and the only place that
reads it back for verification. Everything else goes through
``db.session.tenant_session`` or the repositories, which call in here.

Two properties worth keeping in mind:

* **Fail closed.** An unpinned connection sees zero rows, not every row. A
  forgotten pin is a visible bug (empty result) rather than a silent leak.
* **Transaction scoped.** ``set_config(..., is_local => true)`` binds the value
  to the current transaction, so it cannot survive back into the connection pool
  and bleed into the next request that borrows the same connection.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Postgres GUC the RLS policies read.
TENANT_SETTING: Final[str] = "app.tenant_id"


class TenantNotPinnedError(RuntimeError):
    """Raised when a query is attempted without a tenant bound to the session.

    Not a security failure on its own — RLS already returns nothing — but it
    turns a confusing empty result into a precise error at the call site.
    """


class CrossTenantError(RuntimeError):
    """Raised when code tries to act on a tenant other than the pinned one."""


def coerce_tenant_id(value: str | uuid.UUID) -> uuid.UUID:
    """Normalise a tenant identifier, rejecting anything that is not a UUID.

    The value reaches ``set_config`` as text, so this is also the guard that
    stops a crafted string from being smuggled into the session setting.
    """
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Not a valid tenant id: {value!r}") from exc


async def set_tenant(session: AsyncSession, tenant_id: str | uuid.UUID) -> uuid.UUID:
    """Pin ``session`` to one tenant for the remainder of the transaction.

    Must be called inside a transaction. Outside one, ``is_local => true`` makes
    the setting evaporate immediately and every subsequent query silently
    returns nothing.
    """
    tid = coerce_tenant_id(tenant_id)
    await session.execute(
        text("SELECT set_config(:setting, :value, true)"),
        {"setting": TENANT_SETTING, "value": str(tid)},
    )
    return tid


async def current_tenant(session: AsyncSession) -> uuid.UUID | None:
    """Return the tenant currently pinned, or ``None`` if the session is open."""
    raw = await session.scalar(
        text("SELECT NULLIF(current_setting(:setting, true), '')"),
        {"setting": TENANT_SETTING},
    )
    return uuid.UUID(raw) if raw else None


async def assert_tenant_pinned(session: AsyncSession) -> uuid.UUID:
    """Return the pinned tenant, raising if the session has none."""
    tid = await current_tenant(session)
    if tid is None:
        raise TenantNotPinnedError(
            "No tenant pinned on this session. Every query against a "
            "tenant-scoped table must run inside tenant_session(...) or an "
            "equivalent set_tenant() call, otherwise row-level security "
            "returns no rows."
        )
    return tid


async def clear_tenant(session: AsyncSession) -> None:
    """Unpin the session. Mostly useful in tests that assert fail-closed."""
    await session.execute(
        text("SELECT set_config(:setting, '', true)"), {"setting": TENANT_SETTING}
    )


# -----------------------------------------------------------------------------
# Verification — used by tests and by the deploy smoke check
# -----------------------------------------------------------------------------


async def tables_missing_rls(session: AsyncSession) -> list[str]:
    """Tables that carry ``tenant_id`` but do not have RLS switched on.

    A non-empty result means a migration added a tenant table and forgot to
    protect it. That is the exact mistake this returns rather than waits for.
    """
    rows = await session.execute(
        text(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND NOT c.relrowsecurity
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = c.relname
                      AND column_name = 'tenant_id'
               )
             ORDER BY c.relname
            """
        )
    )
    return [r[0] for r in rows]


async def tables_missing_policy(session: AsyncSession) -> list[str]:
    """RLS-enabled tables with no ``tenant_isolation`` policy attached.

    RLS with no policy denies everything, so this is a availability bug rather
    than a leak — but it is still a bug, and silent until something breaks.
    """
    rows = await session.execute(
        text(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND c.relrowsecurity
               AND NOT EXISTS (
                   SELECT 1 FROM pg_policies p
                    WHERE p.schemaname = 'public'
                      AND p.tablename = c.relname
                      AND p.policyname = 'tenant_isolation'
               )
             ORDER BY c.relname
            """
        )
    )
    return [r[0] for r in rows]


async def roles_that_bypass_rls(session: AsyncSession) -> list[str]:
    """KeenPay roles holding BYPASSRLS.

    Must always be empty. A role with BYPASSRLS defeats every policy in the
    schema at once, and nothing else in the system would notice.
    """
    rows = await session.execute(
        text(
            """
            SELECT rolname FROM pg_roles
             WHERE rolbypassrls AND rolname LIKE 'keenpay%'
             ORDER BY rolname
            """
        )
    )
    return [r[0] for r in rows]


async def verify_rls(session: AsyncSession) -> dict[str, list[str]]:
    """Full RLS posture check. Every value empty means the schema is sound."""
    return {
        "tables_missing_rls": await tables_missing_rls(session),
        "tables_missing_policy": await tables_missing_policy(session),
        "roles_that_bypass_rls": await roles_that_bypass_rls(session),
    }


__all__ = [
    "TENANT_SETTING",
    "CrossTenantError",
    "TenantNotPinnedError",
    "assert_tenant_pinned",
    "clear_tenant",
    "coerce_tenant_id",
    "current_tenant",
    "roles_that_bypass_rls",
    "set_tenant",
    "tables_missing_policy",
    "tables_missing_rls",
    "verify_rls",
]
