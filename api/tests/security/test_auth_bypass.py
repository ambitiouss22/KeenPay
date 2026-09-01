import pytest

pytestmark = pytest.mark.security
from httpx import AsyncClient


@pytest.mark.anyio
async def test_missing_auth_returns_401_not_500(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert "error" in response.json()


@pytest.mark.anyio
async def test_malformed_bearer_rejected(client: AsyncClient):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_security_headers_present(client: AsyncClient):
    response = await client.get("/api/v1/health/live")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Request-ID")


@pytest.mark.anyio
async def test_role_escalation_blocked(client: AsyncClient, shopper_token: str):
    """Shopper cannot create API keys (admin-only)."""
    response = await client.post(
        "/api/v1/auth/api-keys",
        headers={"Authorization": f"Bearer {shopper_token}"},
        json={"name": "evil", "role": "admin"},
    )
    assert response.status_code == 403
