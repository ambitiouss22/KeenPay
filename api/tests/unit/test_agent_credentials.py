"""The agent role, scope narrowing, and the credential the Control Plane mints.

The AI Runtime verifies a credential's audience and scopes locally, but only
the Control Plane can issue one - it holds the signing key. These tests cover
that issuing side and the permission arithmetic behind it.

The property that matters most is in :func:`test_scopes_can_only_narrow`: a
scope list is an intersection with the role, never a union. Get that backwards
and a forged scope claim becomes a privilege escalation.
"""

import pytest

from core.jwt import JWTManager, TokenError
from core.rbac import (
    AGENT_GRANTABLE_SCOPES,
    Permission,
    Role,
    effective_permissions,
    has_permission,
    permissions_for_role,
    permissions_for_scopes,
    require_permission,
)
from services.auth import AuthService

AUDIENCE = "keenpay-control-plane"
FULL_SCOPES = ["catalog:read", "session:create", "authorization:request"]


# --- the role --------------------------------------------------------------


def test_agent_can_do_everything_shopping_requires():
    """Neither shopper nor service could do this alone; that is why it exists."""
    agent = permissions_for_role(Role.AGENT.value)
    for needed in (
        Permission.CATALOG_READ,
        Permission.SESSION_CREATE,
        Permission.SESSION_READ_OWN,
        Permission.ORDER_READ_OWN,
        Permission.AUTHORIZATION_REQUEST,
        Permission.AUTHORIZATION_READ,
    ):
        assert needed in agent, needed


@pytest.mark.parametrize(
    "forbidden",
    [
        Permission.AUTHORIZATION_APPROVE,
        Permission.REFUND_REQUEST,
        Permission.ADMIN_POLICY,
        Permission.ADMIN_USERS,
        Permission.API_KEY_MANAGE,
        Permission.WEBHOOK_INTERNAL,
        Permission.SESSION_READ_ANY,
        Permission.ORDER_READ_ANY,
    ],
)
def test_agent_has_none_of_the_dangerous_permissions(forbidden):
    assert forbidden not in permissions_for_role(Role.AGENT.value)


def test_approve_is_not_a_grantable_scope():
    """No request body can ask for the one permission that blesses money."""
    assert Permission.AUTHORIZATION_APPROVE.value not in AGENT_GRANTABLE_SCOPES
    assert Permission.REFUND_REQUEST.value not in AGENT_GRANTABLE_SCOPES
    assert Permission.API_KEY_MANAGE.value not in AGENT_GRANTABLE_SCOPES


# --- scope arithmetic ------------------------------------------------------


def test_scopes_can_only_narrow():
    """A scope the role lacks grants nothing. This is the escalation guard."""
    assert not has_permission(
        "shopper", Permission.AUTHORIZATION_REQUEST, frozenset({"authorization:request"})
    )
    assert not has_permission(
        "agent", Permission.AUTHORIZATION_APPROVE, frozenset({"authorization:approve"})
    )


def test_a_narrow_scope_removes_what_the_role_allowed():
    scoped = frozenset({"catalog:read"})
    assert has_permission("agent", Permission.CATALOG_READ, scoped)
    assert not has_permission("agent", Permission.SESSION_CREATE, scoped)


def test_no_scopes_means_the_role_alone_decides():
    assert effective_permissions("agent", None) == permissions_for_role("agent")


def test_an_empty_scope_set_permits_nothing():
    """Empty is not the same as absent, and must not be treated as absent."""
    assert effective_permissions("agent", frozenset()) == frozenset()


def test_unknown_scopes_are_ignored_not_fatal():
    """A scope retired after a token was minted narrows it; it does not 500."""
    assert permissions_for_scopes({"catalog:read", "not:a:real:scope"}) == {
        Permission.CATALOG_READ
    }


def test_unscoped_roles_are_unchanged():
    assert has_permission("admin", Permission.AUTHORIZATION_APPROVE)
    assert has_permission("manager", Permission.AUTHORIZATION_APPROVE)
    assert not has_permission("service", Permission.CATALOG_READ)
    assert not has_permission("shopper", Permission.AUTHORIZATION_APPROVE)


def test_a_scope_denial_says_so_rather_than_blaming_the_role():
    """The message has to distinguish "your role can't" from "this token can't"."""
    from core.exceptions import KeenPayError

    with pytest.raises(KeenPayError) as seen:
        require_permission("agent", Permission.SESSION_CREATE, frozenset({"catalog:read"}))
    assert "scope" in str(seen.value)

    with pytest.raises(KeenPayError) as seen:
        require_permission("agent", Permission.AUTHORIZATION_APPROVE, frozenset(FULL_SCOPES))
    assert "lacks permission" in str(seen.value)


# --- the token -------------------------------------------------------------


def test_audience_and_scope_ride_on_the_token():
    manager = JWTManager()
    token = manager.create_access_token(
        user_id="agent_1",
        merchant_id="merchant_keen",
        role="agent",
        audience=AUDIENCE,
        scopes=["session:create", "catalog:read", "catalog:read"],
    )
    claims = manager.decode_access_token(token)

    assert claims.audiences == (AUDIENCE,)
    assert claims.scopes == {"catalog:read", "session:create"}
    # Sorted and de-duplicated, so two equivalent grants are byte-identical.
    assert claims.scope == "catalog:read session:create"


def test_an_ordinary_token_carries_neither():
    manager = JWTManager()
    claims = manager.decode_access_token(
        manager.create_access_token(user_id="u", merchant_id="m", role="admin")
    )
    assert claims.audiences == ()
    assert claims.scopes is None


def test_a_token_for_another_audience_is_refused():
    manager = JWTManager()
    token = manager.create_access_token(
        user_id="u", merchant_id="m", role="agent", audience="some-other-service"
    )
    manager.decode_access_token(token)  # decodable
    with pytest.raises(TokenError, match="audience"):
        manager.decode_access_token(token, audience=AUDIENCE)


def test_the_control_plane_refuses_a_token_minted_for_someone_else():
    """Correctly signed is not the same as intended for us."""
    token = JWTManager().create_access_token(
        user_id="u", merchant_id="m", role="admin", audience="some-other-service"
    )
    with pytest.raises(ValueError, match="not issued for this service"):
        AuthService().verify_access_token(token)


# --- issuing ---------------------------------------------------------------


def test_issued_credential_is_scoped_short_lived_and_agent_roled():
    token, ttl, granted = AuthService().issue_agent_token(
        agent_id="agent_1",
        merchant_id="merchant_keen",
        scopes=FULL_SCOPES,
        issued_by="user_admin",
    )
    assert ttl == 900
    assert granted == sorted(FULL_SCOPES)

    principal = AuthService().verify_access_token(token)
    assert principal.role == "agent"
    assert principal.merchant_id == "merchant_keen"
    assert principal.audience == AUDIENCE
    assert principal.scopes == frozenset(granted)


@pytest.mark.parametrize(
    ("scopes", "message"),
    [
        (["authorization:approve"], "not grantable"),
        (["admin:policy"], "not grantable"),
        (["refund:request"], "not grantable"),
        ([], "at least one scope"),
        (["  "], "at least one scope"),
    ],
)
def test_ungrantable_scopes_are_refused_not_trimmed(scopes, message):
    """Refusing beats silently granting less than was asked for."""
    with pytest.raises(ValueError, match=message):
        AuthService().issue_agent_token(
            agent_id="a", merchant_id="m", scopes=scopes, issued_by="x"
        )


@pytest.mark.parametrize("ttl", [10, 59, 3601, 99999])
def test_ttl_is_bounded_at_both_ends(ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        AuthService().issue_agent_token(
            agent_id="a",
            merchant_id="m",
            scopes=["catalog:read"],
            issued_by="x",
            ttl_seconds=ttl,
        )


def test_the_issued_token_is_accepted_by_the_runtimes_own_parser():
    """The two halves of the contract have to agree, so assert it directly."""
    from ai_runtime.credentials import AgentCredential, CredentialError

    token, _ttl, granted = AuthService().issue_agent_token(
        agent_id="agent_1",
        merchant_id="merchant_keen",
        scopes=FULL_SCOPES,
        issued_by="user_admin",
    )
    credential = AgentCredential.parse(token)
    credential.check(audience=AUDIENCE, required_scopes=set(granted))

    assert credential.merchant_id == "merchant_keen"
    assert credential.role == "agent"
    assert not credential.is_expired()

    with pytest.raises(CredentialError, match="authorization:approve"):
        credential.check(audience=AUDIENCE, required_scopes={"authorization:approve"})
