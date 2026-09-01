"""Unit tests for JWT manager."""

from datetime import timedelta

import pytest
from jose import jwt

from config.settings import Settings
from core.jwt import JWTManager


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
    with pytest.raises(ValueError, match="invalid or expired"):
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
