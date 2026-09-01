"""Pytest fixtures for auth, JWT, and API client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure test settings before app import
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only")
os.environ.setdefault("ENABLE_DEV_ROUTES", "true")
os.environ.setdefault("RAZORPAY_MOCK", "true")
os.environ.setdefault("USE_IN_MEMORY_STORE", "true")

from api.config.settings import get_settings

get_settings.cache_clear()

from api.core.jwt import JWTManager
from api.main import app


@pytest.fixture
def jwt_manager() -> JWTManager:
    return JWTManager()


@pytest.fixture
def shopper_token(jwt_manager: JWTManager) -> str:
    return jwt_manager.create_access_token(
        user_id="user_dev_shopper",
        merchant_id="merchant_keen",
        role="shopper",
    )


@pytest.fixture
def admin_token(jwt_manager: JWTManager) -> str:
    return jwt_manager.create_access_token(
        user_id="user_dev_admin",
        merchant_id="merchant_keen",
        role="admin",
    )


@pytest.fixture
def support_token(jwt_manager: JWTManager) -> str:
    return jwt_manager.create_access_token(
        user_id="user_dev_support",
        merchant_id="merchant_keen",
        role="support_agent",
    )


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(client: AsyncClient, shopper_token: str):
    client.headers.update({"Authorization": f"Bearer {shopper_token}"})
    return client
