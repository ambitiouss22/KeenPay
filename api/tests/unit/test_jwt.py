"""Unit tests for JWT manager."""

from datetime import timedelta

import pytest
from jose import jwt

from config.settings import Settings
from core.jwt import JWTManager, TokenError, TokenExpiredError


@pytest.fixture
def manager() -> JWTManager:
    settings = Settings(jwt_secret="unit-test-secret", jwt_access_expire_minutes=15)
    return JWTManager(settings)


def test_create_and_decode_access_token(manager: JWTManager):
    token = manager.create_access_token(
        user_id="user_abc",
        merchant_id="merchant_keen",
        role="shopper",
    )
    claims = manager.decode_access_token(token)
    assert claims.sub == "user_abc"
    assert claims.merchant_id == "merchant_keen"
    assert claims.role == "shopper"
    assert claims.token_type == "access"


def test_rejects_tampered_token(manager: JWTManager):
    token = manager.create_access_token(
        user_id="user_abc",
        merchant_id="merchant_keen",
        role="shopper",
    )
    payload = jwt.get_unverified_claims(token)
    payload["role"] = "admin"
    bad = jwt.encode(payload, "wrong-secret", algorithm="HS256")
    with pytest.raises(TokenError):
        manager.decode_access_token(bad)


def test_rejects_expired_token(manager: JWTManager):
    settings = Settings(jwt_secret="unit-test-secret")
    mgr = JWTManager(settings)
    token = mgr.create_access_token(
        user_id="user_abc",
        merchant_id="merchant_keen",
        role="shopper",
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(ValueError):
        mgr.decode_access_token(token)


# ---------------------------------------------------------------------------
# Phase 2: the tenant claim, and the forgeries it has to survive
# ---------------------------------------------------------------------------

TENANT = "11111111-1111-1111-1111-111111111111"


def test_tenant_id_round_trips(manager: JWTManager):
    token = manager.create_access_token(
        user_id="u", merchant_id="merchant_keen", role="shopper", tenant_id=TENANT
    )
    assert manager.decode_access_token(token).tenant_id == TENANT


def test_tenant_id_accepts_a_uuid_object(manager: JWTManager):
    import uuid

    token = manager.create_access_token(
        user_id="u", merchant_id="m", role="shopper", tenant_id=uuid.UUID(TENANT)
    )
    assert manager.decode_access_token(token).tenant_id == TENANT


def test_token_without_tenant_still_decodes(manager: JWTManager):
    """Tokens minted before Phase 2 must not stop working on deploy."""
    token = manager.create_access_token(user_id="u", merchant_id="m", role="shopper")
    assert manager.decode_access_token(token).tenant_id is None


def test_extra_claims_cannot_override_tenant_or_role(manager: JWTManager):
    """The whole scheme collapses if a caller can smuggle in its own claims."""
    token = manager.create_access_token(
        user_id="u",
        merchant_id="merchant_keen",
        role="shopper",
        tenant_id=TENANT,
        extra_claims={
            "tenant_id": "99999999-9999-9999-9999-999999999999",
            "role": "admin",
            "sub": "someone_else",
        },
    )
    claims = manager.decode_access_token(token)
    assert claims.tenant_id == TENANT
    assert claims.role == "shopper"
    assert claims.sub == "u"


def test_token_signed_with_another_secret_is_rejected(manager: JWTManager):
    forger = JWTManager(Settings(jwt_secret="attacker-secret-not-the-real-one"))
    forged = forger.create_access_token(
        user_id="u", merchant_id="m", role="admin", tenant_id=TENANT
    )
    with pytest.raises(TokenError):
        manager.decode_access_token(forged)


def test_alg_none_token_is_rejected(manager: JWTManager):
    """Classic algorithm-confusion attack: re-sign with 'none'."""
    payload = {
        "sub": "u",
        "merchant_id": "m",
        "role": "admin",
        "tenant_id": TENANT,
        "iat": 0,
        "exp": 9999999999,
        "token_type": "access",
    }
    import base64
    import json as _json

    def b64(obj: dict) -> str:
        raw = _json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    # python-jose refuses to *encode* alg=none, so assemble it by hand - which
    # is exactly what an attacker would do.
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."
    with pytest.raises(TokenError):
        manager.decode_access_token(unsigned)


def test_refresh_token_cannot_be_used_as_a_bearer(manager: JWTManager):
    token = manager.create_access_token(
        user_id="u", merchant_id="m", role="shopper", extra_claims={"token_type": "refresh"}
    )
    # token_type is written back over extras, so this one is still 'access' -
    # assert the guard itself with a hand-built refresh token.
    hand_rolled = jwt.encode(
        {
            "sub": "u",
            "merchant_id": "m",
            "role": "shopper",
            "iat": 0,
            "exp": 9999999999,
            "token_type": "refresh",
        },
        "unit-test-secret",
        algorithm="HS256",
    )
    assert manager.decode_access_token(token).token_type == "access"
    with pytest.raises(TokenError):
        manager.decode_access_token(hand_rolled)


def test_expiry_raises_the_specific_error(manager: JWTManager):
    token = manager.create_access_token(
        user_id="u", merchant_id="m", role="shopper", expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(TokenExpiredError):
        manager.decode_access_token(token)
    # and it is still a TokenError, so existing `except ValueError` keeps working
    with pytest.raises(ValueError):
        manager.decode_access_token(token)


def test_garbage_is_rejected_not_crashed(manager: JWTManager):
    for junk in ["", "not.a.token", "a.b.c", "Bearer x", "..", "null"]:
        with pytest.raises(TokenError):
            manager.decode_access_token(junk)


def test_each_token_gets_a_unique_jti(manager: JWTManager):
    a = manager.create_access_token(user_id="u", merchant_id="m", role="shopper")
    b = manager.create_access_token(user_id="u", merchant_id="m", role="shopper")
    ja = manager.decode_access_token(a).jti
    jb = manager.decode_access_token(b).jti
    assert ja and jb and ja != jb
