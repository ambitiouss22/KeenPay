"""Authorization decisions: who may do what, and to whose data.

Three separate questions, deliberately kept apart:

``require_role`` / ``require_permission``
    Is this principal allowed to perform this kind of action at all? Answered
    from the role table in :mod:`core.rbac`.

``require_same_tenant``
    Is the record they are reaching for inside their own tenant? Answered by
    comparing against the verified token, never against anything the caller
    sent.

The split matters because they fail differently. A shopper calling an admin
route is a role failure. An admin reaching into another merchant's order is a
tenant failure — and an admin role does not help them, because "admin" is
scoped to a tenant, not to the deployment.

Everything here raises :class:`fastapi.HTTPException` with the API's error
envelope, so a route can call these directly without translating exceptions.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, status

from core.rbac import Permission, Role, has_permission


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"code": code, "message": message}},
    )


def require_role(role: str, *allowed: str | Role) -> None:
    """Allow only the named roles.

    Prefer :func:`require_permission` where a permission exists — it survives
    the role table changing, which a hard-coded list of role names does not.
    """
    wanted = {r.value if isinstance(r, Role) else str(r) for r in allowed}
    if role not in wanted:
        raise _forbidden(
            "FORBIDDEN",
            f"Role '{role}' is not permitted here; requires one of {sorted(wanted)}",
        )


def require_permission(role: str, permission: Permission) -> None:
    """Allow only roles holding ``permission``."""
    if not has_permission(role, permission):
        raise _forbidden(
            "FORBIDDEN", f"Role '{role}' lacks permission '{permission.value}'"
        )


def require_any_permission(role: str, permissions: Iterable[Permission]) -> None:
    """Allow when the role holds at least one of ``permissions``."""
    wanted = list(permissions)
    if not any(has_permission(role, p) for p in wanted):
        names = sorted(p.value for p in wanted)
        raise _forbidden("FORBIDDEN", f"Role '{role}' holds none of {names}")


def require_same_tenant(principal_tenant: str | None, resource_tenant: str | None) -> None:
    """Refuse to act on a record belonging to a different tenant.

    Defence in depth. Row-level security already makes another tenant's rows
    invisible, so a correct query returns nothing rather than reaching this.
    This turns the remaining case — a code path that obtained a record some
    other way — into an explicit 403 instead of a silent leak.

    A missing tenant on either side is refused rather than allowed. Failing
    closed is the only safe default: an unpinned request must not be treated as
    matching everything.
    """
    if not principal_tenant or not resource_tenant:
        raise _forbidden("FORBIDDEN", "Tenant context missing; refusing cross-tenant access")
    if str(principal_tenant) != str(resource_tenant):
        # The message deliberately does not name the resource's tenant. Telling
        # a caller which tenant owns a record confirms the record exists.
        raise _forbidden("FORBIDDEN", "Resource belongs to a different tenant")


def can(role: str, permission: Permission) -> bool:
    """Non-raising check, for branching rather than gating."""
    return has_permission(role, permission)


__all__ = [
    "can",
    "require_any_permission",
    "require_permission",
    "require_role",
    "require_same_tenant",
]
