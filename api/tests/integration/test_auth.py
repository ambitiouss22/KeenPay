"""Integration tests for auth endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_login_success(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "shopper@keenpay.dev",
            "password": "KeenPayDev1!",
            "merchant_id": "merchant_keen",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "shopper"
    assert "access_token" in body
    assert "refresh_token" in body


@pytest.mark.anyio
async def test_login_invalid_credentials(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "nobody@keenpay.dev",
            "password": "wrongpassword1",
            "merchant_id": "merchant_keen",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.anyio
async def test_me_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_me_with_valid_token(client: AsyncClient, shopper_token: str):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {shopper_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user_dev_shopper"
    assert body["role"] == "shopper"


@pytest.mark.anyio
async def test_dev_token_endpoint(client: AsyncClient):
    response = await client.get("/api/v1/dev/token?user_id=user_dev_admin")
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "admin"


@pytest.mark.anyio
async def test_refresh_flow(client: AsyncClient):
    from core.hashing import hash_password
    from repositories import users as users_mod
    from services.auth import AuthService

    users_mod._DEV_USERS["user_test_refresh"] = {
        "id": "user_test_refresh",
        "email": "refresh@keenpay.dev",
        "password_hash": hash_password("RefreshTest1!"),
        "merchant_id": "merchant_keen",
        "role": "shopper",
        "active": True,
        "locked_until": None,
        "failed_login_count": 0,
        "last_login_at": None,
    }
    users_mod._DEV_EMAIL_INDEX["merchant_keen:refresh@keenpay.dev"] = users_mod._DEV_USERS[
        "user_test_refresh"
    ]

    auth = AuthService()
    _, refresh, _ = await auth.authenticate_password(
        email="refresh@keenpay.dev",
        password="RefreshTest1!",
        merchant_id="merchant_keen",
    )
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "refresh_token" in response.json()


@pytest.mark.anyio
async def test_api_key_create_requires_admin(
    client: AsyncClient, shopper_token: str, admin_token: str
):
    denied = await client.post(
        "/api/v1/auth/api-keys",
        headers={"Authorization": f"Bearer {shopper_token}"},
        json={"name": "test", "role": "service"},
    )
    assert denied.status_code == 403

    allowed = await client.post(
        "/api/v1/auth/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "ci-key", "role": "service"},
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["api_key"].startswith("kp_")
