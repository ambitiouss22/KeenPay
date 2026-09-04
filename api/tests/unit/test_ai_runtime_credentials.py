"""Agent credential parsing and the checks that gate its use."""

import time

import pytest

from ai_runtime.credentials import AgentCredential, CredentialError

AUDIENCE = "keenpay-control-plane"


def test_parses_claims(agent_token):
    cred = AgentCredential.parse(agent_token)
    assert cred.subject == "agent_buyer_1"
    assert cred.merchant_id == "merchant_keen"
    assert cred.role == "service"
    assert AUDIENCE in cred.audience
    assert "authorization:request" in cred.scopes


def test_repr_never_leaks_the_token(agent_token):
    """A credential lands in logs and tracebacks; the bearer value must not."""
    cred = AgentCredential.parse(agent_token)
    text = repr(cred)
    assert agent_token not in text
    assert "redacted" in text


def test_empty_token_is_refused():
    with pytest.raises(CredentialError, match="empty"):
        AgentCredential.parse("   ")


def test_malformed_token_is_refused():
    with pytest.raises(CredentialError, match="well-formed"):
        AgentCredential.parse("not.a-jwt")


def test_unreadable_payload_is_refused():
    with pytest.raises(CredentialError, match="readable JSON"):
        AgentCredential.parse("aGVhZGVy.bm90LWpzb24.sig")


def test_expired_token_is_refused(make_agent_token):
    token = make_agent_token(expires_in=-1)
    cred = AgentCredential.parse(token)
    assert cred.is_expired()
    with pytest.raises(CredentialError, match="expired"):
        cred.check(audience=AUDIENCE)


def test_token_without_expiry_counts_as_expired(make_agent_token):
    """A credential that never dies is not the short-lived one the design asks for."""
    cred = AgentCredential.parse(make_agent_token(expires_in=None))
    assert cred.expires_at is None
    assert cred.is_expired()
    with pytest.raises(CredentialError):
        cred.check(audience=AUDIENCE)


def test_wrong_audience_is_refused(make_agent_token):
    cred = AgentCredential.parse(make_agent_token(audience="some-other-service"))
    with pytest.raises(CredentialError, match="audience"):
        cred.check(audience=AUDIENCE)


def test_audience_may_be_a_list(make_agent_token):
    cred = AgentCredential.parse(make_agent_token(audience=["other", AUDIENCE]))
    cred.check(audience=AUDIENCE)


def test_missing_scope_is_refused_before_the_call(make_agent_token):
    """The failure names the missing scope, which a 403 from the far end would not."""
    cred = AgentCredential.parse(make_agent_token(scopes="catalog:read"))
    with pytest.raises(CredentialError, match="authorization:request"):
        cred.check(audience=AUDIENCE, required_scopes={"authorization:request"})


def test_space_separated_and_list_scopes_both_parse(make_agent_token):
    space = AgentCredential.parse(make_agent_token(scopes="catalog:read session:create"))
    assert space.scopes == {"catalog:read", "session:create"}


def test_leeway_rejects_a_token_about_to_expire(make_agent_token):
    """A token with two seconds left should not start a run that takes ten."""
    cred = AgentCredential.parse(make_agent_token(expires_in=2))
    assert not cred.is_expired()
    assert cred.is_expired(leeway_seconds=30)


def test_seconds_remaining_tracks_expiry(make_agent_token):
    cred = AgentCredential.parse(make_agent_token(expires_in=300))
    remaining = cred.seconds_remaining(now=time.time())
    assert remaining is not None
    assert 280 < remaining <= 300
