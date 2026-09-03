"""Role-based access control for KeenPay API."""

from enum import Enum
from functools import lru_cache


class Role(str, Enum):
    SHOPPER = "shopper"
    SUPPORT_AGENT = "support_agent"
    MANAGER = "manager"
    ADMIN = "admin"
    SERVICE = "service"


class Permission(str, Enum):
    SESSION_CREATE = "session:create"
    SESSION_READ_OWN = "session:read:own"
    SESSION_READ_ANY = "session:read:any"
    CATALOG_READ = "catalog:read"
    ORDER_READ_OWN = "order:read:own"
    ORDER_READ_ANY = "order:read:any"
    AUDIT_READ = "audit:read"
    ESCALATION_READ = "escalation:read"
    ESCALATION_RESOLVE = "escalation:resolve"
    ADMIN_ESCALATION = "admin:escalation"
    ADMIN_POLICY = "admin:policy"
    ADMIN_USERS = "admin:users"
    API_KEY_MANAGE = "admin:api_keys"
    WEBHOOK_INTERNAL = "webhook:internal"
    DEV_SIMULATE = "dev:simulate"
    # --- phase 5: the money gate ---
    # Request, read and approve are three permissions rather than one, so that
    # separation of duties is expressible: the party that asks for money to
    # move is not the party that blesses it, and an investigator can read every
    # record without being able to approve any of them.
    POLICY_EVALUATE = "policy:evaluate"
    AUTHORIZATION_REQUEST = "authorization:request"
    AUTHORIZATION_READ = "authorization:read"
    AUTHORIZATION_APPROVE = "authorization:approve"
    REFUND_REQUEST = "refund:request"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SHOPPER: frozenset(
        {
            Permission.SESSION_CREATE,
            Permission.SESSION_READ_OWN,
            Permission.CATALOG_READ,
            Permission.ORDER_READ_OWN,
        }
    ),
    Role.SUPPORT_AGENT: frozenset(
        {
            Permission.SESSION_READ_ANY,
            Permission.CATALOG_READ,
            Permission.ORDER_READ_ANY,
            Permission.AUDIT_READ,
            Permission.ESCALATION_READ,
            # Support can see why money was held, and can approve nothing.
            Permission.AUTHORIZATION_READ,
        }
    ),
    Role.MANAGER: frozenset(
        {
            Permission.SESSION_READ_ANY,
            Permission.CATALOG_READ,
            Permission.ORDER_READ_ANY,
            Permission.AUDIT_READ,
            Permission.ESCALATION_READ,
            Permission.ESCALATION_RESOLVE,
            Permission.ADMIN_ESCALATION,
            Permission.POLICY_EVALUATE,
            Permission.AUTHORIZATION_REQUEST,
            Permission.AUTHORIZATION_READ,
            # Manager is the lowest role that may approve money movement.
            Permission.AUTHORIZATION_APPROVE,
            Permission.REFUND_REQUEST,
        }
    ),
    Role.ADMIN: frozenset(Permission),
    Role.SERVICE: frozenset(
        {
            Permission.WEBHOOK_INTERNAL,
            Permission.SESSION_READ_ANY,
            Permission.ORDER_READ_ANY,
            Permission.AUDIT_READ,
            # A service account may ask for an authorization and read it back.
            # It may never approve one: an approval is a human act, and a
            # leaked service key that could approve its own requests would
            # make the whole gate ceremonial.
            Permission.POLICY_EVALUATE,
            Permission.AUTHORIZATION_REQUEST,
            Permission.AUTHORIZATION_READ,
        }
    ),
}


@lru_cache
def permissions_for_role(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return frozenset()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for_role(role)


def require_permission(role: str, permission: Permission) -> None:
    if not has_permission(role, permission):
        from core.exceptions import KeenPayError

        raise KeenPayError(
            code="FORBIDDEN",
            message=f"Role '{role}' lacks permission '{permission.value}'",
        )
