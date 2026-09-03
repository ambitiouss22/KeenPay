"""Holding a permission is not the same as being allowed to touch a record.

These tests exist because of a real hole found by driving the API end to end:
every ``/sessions/{id}`` route checked ``SESSION_READ_OWN`` and then served the
record to anyone who asked. The permission name promised ownership; nothing
enforced it. A shopper at one merchant could read - and confirm payment on -
another merchant's session by knowing its id.

Row-level security does not help here: these routes read the in-memory session
store, not a tenant-pinned database session. Authorization at the object level
has to be explicit.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from core.jwt import JWTManager

pytestmark = pytest.mark.asyncio

PASSWORD = "KeenPayDev1!"


@pytest.fixture
def app():
    from main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def victim(client):
    """A logged-in shopper who owns a session."""
    r = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "shopper@keenpay.dev",
            "password": PASSWORD,
            "merchant_id": "merchant_keen",
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/sessions", headers=headers, json={"merchant_id": "merchant_keen"}
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["session_id"]


def other(*, user_id: str, merchant: str = "merchant_keen", role: str = "shopper") -> dict:
    """Headers for some other principal."""
    token = JWTManager().create_access_token(
        user_id=user_id, merchant_id=merchant, role=role
    )
    return {"Authorization": f"Bearer {token}"}


def routes(session_id: str):
    """Every route that takes a session id, with a body that reaches the handler."""
    return [
        ("GET", f"/api/v1/sessions/{session_id}", None),
        ("GET", f"/api/v1/sessions/{session_id}/audit", None),
        ("POST", f"/api/v1/sessions/{session_id}/messages", {"text": "hello"}),
        (
            "POST",
            f"/api/v1/sessions/{session_id}/confirm",
            {"confirmed": True, "idempotency_key": "k-probe"},
        ),
    ]


# --- the owner keeps working -----------------------------------------------


async def test_owner_can_use_every_session_route(client, victim):
    headers, sid = victim
    for method, url, body in routes(sid):
        r = await client.request(method, url, headers=headers, json=body)
        assert r.status_code < 400, f"{method} {url} -> {r.status_code} for the owner"


# --- another user, same merchant -------------------------------------------


@pytest.mark.parametrize("method,url_t,body", routes("{sid}"))
async def test_another_shopper_cannot_reach_the_session(client, victim, method, url_t, body):
    _, sid = victim
    r = await client.request(
        method, url_t.format(sid=sid), headers=other(user_id="user_someone_else"), json=body
    )
    assert r.status_code == 404, f"{method} -> {r.status_code}, expected 404"


# --- another merchant -------------------------------------------------------


@pytest.mark.parametrize("method,url_t,body", routes("{sid}"))
async def test_another_merchant_cannot_reach_the_session(client, victim, method, url_t, body):
    _, sid = victim
    r = await client.request(
        method,
        url_t.format(sid=sid),
        headers=other(user_id="user_acme", merchant="merchant_acme"),
        json=body,
    )
    assert r.status_code == 404, f"{method} -> {r.status_code}, expected 404"


async def test_admin_of_another_merchant_is_still_refused(client, victim):
    """Role power must stop at the tenant edge, admin included."""
    _, sid = victim
    r = await client.get(
        f"/api/v1/sessions/{sid}",
        headers=other(user_id="admin_acme", merchant="merchant_acme", role="admin"),
    )
    assert r.status_code == 404


# --- no existence disclosure ------------------------------------------------


async def test_forbidden_and_missing_are_indistinguishable(client, victim):
    """A 403 would confirm the id is real. Both must look identical."""
    _, sid = victim
    intruder = other(user_id="user_someone_else")

    real = await client.get(f"/api/v1/sessions/{sid}", headers=intruder)
    fake = await client.get("/api/v1/sessions/sess_does_not_exist", headers=intruder)

    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()
