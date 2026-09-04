"""Role-based access control for KeenPay API."""

from enum import Enum
from functools import lru_cache


class Role(str, Enum):
    SHOPPER = "shopper"
    SUPPORT_AGENT = "support_agent"
    MANAGER = "manager"
    ADMIN = "admin"
    SERVICE = "service"
    #: An autonomous buyer acting on a shopper's behalf. Neither existing role
    #: fits: a shopper may build a cart but cannot ask for an authorization, and
    #: a service account may ask for one but cannot browse or build a cart. An
    #: agent needs the union of those two halves - and, just as importantly,
    #: nothing beyond it.
    AGENT = "agent"


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
    # --- the money gate ---
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
    Role.AGENT: frozenset(
        {
            # Enough to shop: read the catalogue, build a cart, turn it into an
            # order, ask for that order to be authorized, and read the answer.
            Permission.CATALOG_READ,
            Permission.SESSION_CREATE,
            Permission.SESSION_READ_OWN,
            Permission.ORDER_READ_OWN,
            Permission.AUTHORIZATION_REQUEST,
            Permission.AUTHORIZATION_READ,
            # Deliberately absent, and the absence is the whole point:
            # AUTHORIZATION_APPROVE, REFUND_REQUEST, ADMIN_POLICY,
            # ADMIN_USERS, API_KEY_MANAGE, WEBHOOK_INTERNAL. An agent asks;
            # it never blesses, refunds, or reconfigures anything.
        }
    ),
}

#: Which permission each agent scope string grants. Scopes are the vocabulary
#: the AI Runtime speaks; permissions are the vocabulary the routers enforce.
#: Keeping an explicit map means a scope cannot silently acquire meaning by
#: someone renaming a permission.
SCOPE_PERMISSIONS: dict[str, Permission] = {
    Permission.CATALOG_READ.value: Permission.CATALOG_READ,
    Permission.SESSION_CREATE.value: Permission.SESSION_CREATE,
    Permission.SESSION_READ_OWN.value: Permission.SESSION_READ_OWN,
    Permission.ORDER_READ_OWN.value: Permission.ORDER_READ_OWN,
    Permission.AUTHORIZATION_REQUEST.value: Permission.AUTHORIZATION_REQUEST,
    Permission.AUTHORIZATION_READ.value: Permission.AUTHORIZATION_READ,
}

#: The most any agent credential may ever be granted. A request naming a scope
#: outside this set is refused rather than trimmed, so an operator who asks for
#: something impossible is told so instead of quietly getting less.
AGENT_GRANTABLE_SCOPES: frozenset[str] = frozenset(SCOPE_PERMISSIONS)


@lru_cache
def permissions_for_role(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return frozenset()


def permissions_for_scopes(scopes: frozenset[str] | set[str] | tuple[str, ...]) -> (
    frozenset[Permission]
):
    """Translate scope strings into permissions, ignoring unknown ones.

    Unknown scopes are dropped rather than raising, because this runs on the
    request path against a token that was minted earlier: a scope removed from
    the map after issue should narrow the token, never 500 it.
    """
    return frozenset(
        SCOPE_PERMISSIONS[scope] for scope in scopes if scope in SCOPE_PERMISSIONS
    )


def effective_permissions(
    role: str, scopes: frozenset[str] | set[str] | tuple[str, ...] | None = None
) -> frozenset[Permission]:
    """What the caller may actually do.

    A token's scopes can only *narrow* its role, never widen it - the result is
    an intersection. That ordering is what makes a scoped credential safe to
    hand to a component that might be compromised: the worst a forged scope
    list can achieve is asking for something the role already allowed.
    """
    granted = permissions_for_role(role)
    if scopes is None:
        return granted
    return granted & permissions_for_scopes(scopes)


def has_permission(
    role: str,
    permission: Permission,
    scopes: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> bool:
    return permission in effective_permissions(role, scopes)


def require_permission(
    role: str,
    permission: Permission,
    scopes: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> None:
    if not has_permission(role, permission, scopes):
        from core.exceptions import KeenPayError

        if scopes is not None and permission in permissions_for_role(role):
            raise KeenPayError(
                code="FORBIDDEN",
                message=f"Credential scope does not include '{permission.value}'",
            )
        raise KeenPayError(
            code="FORBIDDEN",
            message=f"Role '{role}' lacks permission '{permission.value}'",
        )
