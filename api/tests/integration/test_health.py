"""Integration tests for health endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_health(client: AsyncClient):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert "components" in body


@pytest.mark.anyio
async def test_liveness(client: AsyncClient):
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


@pytest.mark.anyio
async def test_readiness(client: AsyncClient):
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert "status" in response.json()


@pytest.mark.asyncio
async def test_metrics(client: AsyncClient):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "keenpay_up" in response.text
