"""Unit tests for RBAC."""

import pytest

from core.exceptions import KeenPayError
from core.rbac import Permission, has_permission, require_permission


@pytest.mark.parametrize(
    "role,permission,expected",
    [
        ("shopper", Permission.SESSION_CREATE, True),
        ("shopper", Permission.ADMIN_USERS, False),
        ("support_agent", Permission.ESCALATION_READ, True),
        ("support_agent", Permission.ESCALATION_RESOLVE, False),
        ("manager", Permission.ESCALATION_RESOLVE, True),
        ("admin", Permission.API_KEY_MANAGE, True),
        ("service", Permission.WEBHOOK_INTERNAL, True),
        ("invalid_role", Permission.CATALOG_READ, False),
    ],
)
def test_has_permission(role, permission, expected):
    assert has_permission(role, permission) is expected


def test_require_permission_raises():
    with pytest.raises(KeenPayError) as exc:
        require_permission("shopper", Permission.ADMIN_USERS)
    assert exc.value.code == "FORBIDDEN"
