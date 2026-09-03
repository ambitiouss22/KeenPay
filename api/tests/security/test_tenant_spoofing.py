"""Tenant identity must come from the token and nowhere else.

Phase 2's acceptance criteria in test form:

    unauthenticated -> 401
    wrong tenant    -> 403 or empty
    no tenant spoofing

The last one is the reason the middleware strips headers rather than merely
ignoring them. A test that only checks "the handler ignores X-Tenant-ID" passes
right up until someone writes a handler that reads it. Asserting the header is
*gone by the time any handler runs* is a property that survives future code.
"""

from __future__ import annotations

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from core.jwt import JWTManager
from middleware.middleware import SPOOFABLE_TENANT_HEADERS

pytestmark = pytest.mark.asyncio

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def app():
    """The real application, with the real middleware stack."""
    from main import create_app

    return create_app()


@pytest.fixture
async def probe_app(app):
    """The app plus one route that reports what the request actually carries.

    Added after create_app() so it sits behind the same middleware as every
    other route - which is the point: it observes what a handler would see.
    """

    @app.get("/__probe__")
    async def _probe(request: Request):  # pragma: no cover - via the client
        return {
            "seen_headers": sorted(
                k for k in request.headers if k.lower() in SPOOFABLE_TENANT_HEADERS
            ),
            "state_tenant_id": getattr(request.state, "tenant_id", None),
            "state_merchant_id": getattr(request.state, "merchant_id", None),
            "state_role": getattr(request.state, "role", None),
        }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def token(*, tenant_id=TENANT_A, role="shopper", merchant="merchant_keen") -> str:
    return JWTManager().create_access_token(
        user_id="user_dev_shopper", merchant_id=merchant, role=role, tenant_id=tenant_id
    )


# --- the headers never arrive ----------------------------------------------


@pytest.mark.parametrize("header", SPOOFABLE_TENANT_HEADERS)
async def test_tenant_headers_are_stripped_before_any_handler(probe_app, header):
    r = await probe_app.get("/__probe__", headers={header: TENANT_B})
    assert r.status_code == 200
    assert r.json()["seen_headers"] == []


async def test_stripping_survives_odd_casing(probe_app):
    r = await probe_app.get(
        "/__probe__", headers={"X-TeNaNt-Id": TENANT_B, "X-MERCHANT-ID": "merchant_acme"}
    )
    assert r.json()["seen_headers"] == []


async def test_many_spoof_headers_at_once_are_all_stripped(probe_app):
    headers = {h: TENANT_B for h in SPOOFABLE_TENANT_HEADERS}
    r = await probe_app.get("/__probe__", headers=headers)
    assert r.json()["seen_headers"] == []


# --- context comes from the token ------------------------------------------


async def test_context_is_empty_without_a_token(probe_app):
    body = (await probe_app.get("/__probe__")).json()
    assert body["state_tenant_id"] is None
    assert body["state_role"] is None


async def test_context_is_populated_from_the_token(probe_app):
    body = (
        await probe_app.get(
            "/__probe__", headers={"Authorization": f"Bearer {token()}"}
        )
    ).json()
    assert body["state_tenant_id"] == TENANT_A
    assert body["state_role"] == "shopper"


async def test_header_cannot_override_the_token(probe_app):
    """The core attack: a valid token for A, plus a header claiming B."""
    body = (
        await probe_app.get(
            "/__probe__",
            headers={
                "Authorization": f"Bearer {token(tenant_id=TENANT_A)}",
                "X-Tenant-ID": TENANT_B,
                "X-Merchant-ID": "merchant_acme",
            },
        )
    ).json()
    assert body["state_tenant_id"] == TENANT_A
    assert body["state_merchant_id"] == "merchant_keen"
    assert body["seen_headers"] == []


async def test_header_cannot_supply_a_tenant_when_there_is_no_token(probe_app):
    body = (await probe_app.get("/__probe__", headers={"X-Tenant-ID": TENANT_B})).json()
    assert body["state_tenant_id"] is None


async def test_forged_token_yields_no_context(probe_app):
    from config.settings import Settings

    forged = JWTManager(Settings(jwt_secret="attacker-secret")).create_access_token(
        user_id="mallory", merchant_id="merchant_acme", role="admin", tenant_id=TENANT_B
    )
    body = (
        await probe_app.get("/__probe__", headers={"Authorization": f"Bearer {forged}"})
    ).json()
    assert body["state_tenant_id"] is None
    assert body["state_role"] is None


async def test_expired_token_yields_no_context(probe_app):
    from datetime import timedelta

    stale = JWTManager().create_access_token(
        user_id="u",
        merchant_id="merchant_keen",
        role="admin",
        tenant_id=TENANT_A,
        expires_delta=timedelta(seconds=-1),
    )
    body = (
        await probe_app.get("/__probe__", headers={"Authorization": f"Bearer {stale}"})
    ).json()
    assert body["state_tenant_id"] is None


# --- acceptance: unauthenticated -> 401 ------------------------------------


# Real, mounted, permission-gated routes. Deliberately NOT tolerant of 404/405:
# a path that does not exist would make this pass while proving nothing.
PROTECTED = [
    "/api/v1/catalog/products",
    "/api/v1/orders/ord_whatever",
    "/api/v1/sessions/sess_whatever",
    "/api/v1/admin/escalations",
]


@pytest.mark.parametrize("path", PROTECTED)
async def test_protected_routes_require_authentication(app, path):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(path)
    assert r.status_code == 401, f"{path} answered {r.status_code}, expected 401"
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_a_spoof_header_does_not_authenticate_anything(app):
    """Headers must not be a way in, only a way to be ignored."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(
            "/api/v1/catalog/products",
            headers={"X-Tenant-ID": TENANT_B, "X-Merchant-ID": "merchant_acme"},
        )
    assert r.status_code == 401
