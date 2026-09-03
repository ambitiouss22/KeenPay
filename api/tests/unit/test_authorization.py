"""Authorization decisions, including the attacks they exist to stop.

Two distinct failures are checked here, because conflating them is how
cross-tenant bugs get shipped:

  * role failure   - this principal may not perform this kind of action
  * tenant failure - this principal may not touch this particular record

An admin passes every role check and must still fail the tenant check, because
"admin" is scoped to one merchant, not to the deployment.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.authorization import (
    can,
    require_any_permission,
    require_permission,
    require_role,
    require_same_tenant,
)
from core.rbac import Permission, Role

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _code(exc: HTTPException) -> str:
    return exc.detail["error"]["code"]


# --- roles ------------------------------------------------------------------


def test_permitted_role_passes():
    require_role("admin", Role.ADMIN)


def test_role_accepts_plain_strings_too():
    require_role("manager", "manager", "admin")


def test_wrong_role_is_403_not_401():
    """401 means "who are you"; this caller is known and simply not allowed."""
    with pytest.raises(HTTPException) as exc:
        require_role("shopper", Role.ADMIN)
    assert exc.value.status_code == 403
    assert _code(exc.value) == "FORBIDDEN"


def test_unknown_role_is_refused():
    with pytest.raises(HTTPException):
        require_role("wizard", Role.ADMIN)


def test_empty_role_is_refused():
    with pytest.raises(HTTPException):
        require_role("", Role.ADMIN)


# --- permissions ------------------------------------------------------------


def test_role_holding_the_permission_passes():
    require_permission("shopper", Permission.SESSION_CREATE)


def test_role_lacking_the_permission_is_refused():
    with pytest.raises(HTTPException) as exc:
        require_permission("shopper", Permission.ADMIN_USERS)
    assert exc.value.status_code == 403


def test_admin_holds_every_permission():
    for perm in Permission:
        require_permission("admin", perm)


def test_any_permission_passes_when_one_matches():
    require_any_permission("shopper", [Permission.ADMIN_USERS, Permission.CATALOG_READ])


def test_any_permission_refuses_when_none_match():
    with pytest.raises(HTTPException):
        require_any_permission("shopper", [Permission.ADMIN_USERS, Permission.ADMIN_POLICY])


def test_can_reports_without_raising():
    assert can("shopper", Permission.CATALOG_READ) is True
    assert can("shopper", Permission.ADMIN_USERS) is False


# --- privilege escalation ---------------------------------------------------


@pytest.mark.parametrize(
    "role,forbidden",
    [
        ("shopper", Permission.ADMIN_USERS),
        ("shopper", Permission.ADMIN_POLICY),
        ("shopper", Permission.ORDER_READ_ANY),
        ("shopper", Permission.AUDIT_READ),
        ("support_agent", Permission.ADMIN_USERS),
        ("support_agent", Permission.ESCALATION_RESOLVE),
        ("manager", Permission.ADMIN_USERS),
        ("manager", Permission.API_KEY_MANAGE),
        ("service", Permission.ADMIN_USERS),
        ("service", Permission.SESSION_CREATE),
    ],
)
def test_lower_roles_cannot_reach_higher_permissions(role, forbidden):
    with pytest.raises(HTTPException):
        require_permission(role, forbidden)


@pytest.mark.parametrize("spoof", ["admin ", " admin", "ADMIN", "Admin", "admin\n", "adm in"])
def test_role_matching_is_exact(spoof):
    """No trimming or case-folding: 'ADMIN' must not become 'admin'."""
    with pytest.raises(HTTPException):
        require_permission(spoof, Permission.ADMIN_USERS)
    with pytest.raises(HTTPException):
        require_role(spoof, Role.ADMIN)


# --- tenant boundary --------------------------------------------------------


def test_same_tenant_passes():
    require_same_tenant(TENANT_A, TENANT_A)


def test_cross_tenant_is_refused():
    with pytest.raises(HTTPException) as exc:
        require_same_tenant(TENANT_A, TENANT_B)
    assert exc.value.status_code == 403


def test_admin_role_does_not_cross_the_tenant_boundary():
    """The headline property: role power stops at the tenant edge."""
    require_permission("admin", Permission.ORDER_READ_ANY)  # allowed in-tenant
    with pytest.raises(HTTPException):
        require_same_tenant(TENANT_A, TENANT_B)  # still refused across tenants


@pytest.mark.parametrize(
    "principal,resource",
    [(None, TENANT_A), (TENANT_A, None), (None, None), ("", TENANT_A), (TENANT_A, "")],
)
def test_missing_tenant_fails_closed(principal, resource):
    """An unpinned request must not be treated as matching everything."""
    with pytest.raises(HTTPException):
        require_same_tenant(principal, resource)


def test_tenant_comparison_tolerates_uuid_objects():
    import uuid

    tid = uuid.UUID(TENANT_A)
    require_same_tenant(tid, TENANT_A)
    with pytest.raises(HTTPException):
        require_same_tenant(tid, TENANT_B)


def test_error_does_not_disclose_the_other_tenant():
    """Naming the owning tenant would confirm the record exists."""
    with pytest.raises(HTTPException) as exc:
        require_same_tenant(TENANT_A, TENANT_B)
    assert TENANT_B not in str(exc.value.detail)
