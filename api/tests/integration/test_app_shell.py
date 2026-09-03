"""Phase 3 acceptance: the application shell.

    app boots
    unhandled error -> 500 problem JSON carrying request_id
    health probes answer correctly

The unhandled-error tests use ``raise_app_exceptions=False``. Starlette's
ServerErrorMiddleware builds the 500 response *and* re-raises so the server can
log the traceback; without that flag the test client sees the re-raise instead
of the response a real client would receive.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# No module-level asyncio mark: pyproject sets asyncio_mode = "auto", so async
# tests are detected automatically, and a blanket mark would also be applied to
# the sync tests here and warn on each one.


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
async def tolerant(app):
    """A client that returns the 500 rather than re-raising."""

    @app.get("/__explode__")
    async def _explode():  # pragma: no cover - driven through the client
        raise RuntimeError("db://user:hunter2@internal-host/secret")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


# --- boot -------------------------------------------------------------------


async def test_app_boots_and_serves_its_schema(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "KeenPay API"


def test_router_registry_mounted_everything(app):
    mounted = app.state.mounted_routers
    for expected in ("routers.health", "routers.auth", "routers.carts", "routers.products"):
        assert expected in mounted, f"{expected} not mounted: {mounted}"


def test_registry_tolerates_a_router_that_does_not_exist_yet(app):
    """Later phases pre-register routers; a missing module must not stop boot."""
    from fastapi import FastAPI

    from config.settings import get_settings
    from routers.router import Mount, register_routers

    probe = FastAPI()
    import routers.router as reg

    original = reg.REGISTRY
    try:
        reg.REGISTRY = [Mount("routers.not_written_yet")]
        report = register_routers(probe, get_settings())
    finally:
        reg.REGISTRY = original

    assert report.missing == ["routers.not_written_yet"]
    assert report.mounted == []


# --- error envelope ---------------------------------------------------------


async def test_unhandled_error_is_a_500_with_a_request_id(tolerant):
    r = await tolerant.get("/__explode__")
    assert r.status_code == 500
    error = r.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["request_id"], "a 500 without a request id cannot be traced"


async def test_a_500_does_not_leak_internals(tolerant):
    """The exception text held a connection string; the response must not."""
    r = await tolerant.get("/__explode__")
    body = r.text
    for secret in ("hunter2", "internal-host", "RuntimeError", "Traceback"):
        assert secret not in body, f"{secret!r} leaked into the response"


async def test_the_request_id_is_echoed_in_the_header_too(tolerant):
    r = await tolerant.get("/__explode__")
    assert r.headers["X-Request-ID"] == r.json()["error"]["request_id"]


async def test_every_error_shares_one_envelope(client):
    """401, 404 and 422 must be parseable by the same client code."""
    for response in (
        await client.get("/api/v1/catalog/products"),  # 401
        await client.get("/api/v1/products/NOPE"),  # 401 (unauthenticated)
        await client.post("/api/v1/auth/login", json={}),  # 422
    ):
        body = response.json()
        assert "error" in body, body
        assert "code" in body["error"]
        assert "message" in body["error"]


async def test_validation_errors_use_the_envelope_not_fastapi_default(client):
    r = await client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "detail" not in r.json(), "FastAPI's default shape leaked through"


def test_domain_errors_carry_their_own_status():
    """The old handler guessed 400 for everything that was not FORBIDDEN."""
    from core.exceptions import (
        AuthenticationError,
        ConflictError,
        DependencyError,
        NotFoundError,
        ValidationError,
    )

    assert NotFoundError().status_code == 404
    assert ConflictError().status_code == 409
    assert ValidationError().status_code == 422
    assert AuthenticationError().status_code == 401
    assert DependencyError().status_code == 502


def test_exception_envelope_includes_details_only_when_present():
    from core.exceptions import ValidationError

    assert "details" not in ValidationError("X", "m").to_envelope()["error"]
    assert ValidationError("X", "m", {"f": 1}).to_envelope()["error"]["details"] == {"f": 1}


# --- health probes ----------------------------------------------------------


async def test_liveness_never_touches_a_dependency(client):
    """Liveness must not fail because Postgres is down, or a blip restarts us."""
    r = await client.get("/api/v1/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


async def test_readiness_reports_its_verdict_in_the_status_code(client):
    r = await client.get("/api/v1/health/ready")
    assert r.status_code in (200, 503)
    body = r.json()
    # The pairing is the point: a readiness probe that always answered 200
    # would keep a broken instance in the load balancer.
    assert (r.status_code == 200) == (body["status"] == "ready")


async def test_readiness_reports_the_rls_posture(client):
    assert "rls" in (await client.get("/api/v1/health/ready")).json()


async def test_health_reports_components(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body["components"]) == {"postgresql", "redis", "razorpay", "llm"}
    assert 0 <= body["degradation_level"] <= 3


async def test_health_endpoints_are_public(client):
    for path in ("/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready"):
        assert (await client.get(path)).status_code != 401


# --- logging ----------------------------------------------------------------


def test_secrets_are_redacted_from_logs():
    from core.logging import _redact

    event = _redact(
        None,
        None,
        {"password": "hunter2", "access_token": "eyJ...", "user_id": "u1", "event": "login"},
    )
    assert event["password"] == "[redacted]"
    assert event["access_token"] == "[redacted]"
    assert event["user_id"] == "u1", "non-secret fields must survive"
