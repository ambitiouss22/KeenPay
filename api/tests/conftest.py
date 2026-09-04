"""Pytest fixtures for auth, JWT, and API client."""

import base64
import json
import os
import time
from typing import Any

import httpx
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


# ---------------------------------------------------------------------------
# AI Runtime support
#
# The runtime is a separate service with its own boundary, so its tests get
# their own fixtures. Two things matter about how they are built.
#
# The agent token is assembled by hand rather than signed. The runtime cannot
# verify signatures - it holds no key - so a signed token would test nothing
# the runtime does, while making the test depend on a signing secret the
# service is defined by not having.
#
# The stub Control Plane is an httpx transport, not a mocked client. That keeps
# the real allowlist, the real credential checks and the real request building
# in the path under test; mocking the client would leave all three unexercised
# and the tests would pass with the boundary removed.
# ---------------------------------------------------------------------------


def _b64(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


AGENT_SCOPE_STRING = (
    "catalog:read session:create order:read:own authorization:request authorization:read"
)


@pytest.fixture
def make_agent_token():
    """Build an agent credential with whatever claims a test needs."""

    def _make(
        *,
        audience: str | list[str] = "keenpay-control-plane",
        scopes: str | None = AGENT_SCOPE_STRING,
        expires_in: int | None = 300,
        merchant_id: str = "merchant_keen",
        subject: str = "agent_buyer_1",
        role: str = "service",
    ) -> str:
        claims: dict[str, Any] = {
            "sub": subject,
            "merchant_id": merchant_id,
            "role": role,
            "token_type": "access",
            "iat": int(time.time()),
        }
        if audience is not None:
            claims["aud"] = audience
        if scopes is not None:
            claims["scope"] = scopes
        if expires_in is not None:
            claims["exp"] = int(time.time()) + expires_in
        return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(claims)}.not-a-real-signature"

    return _make


@pytest.fixture
def agent_token(make_agent_token) -> str:
    return make_agent_token()


CATALOG_FIXTURE: list[dict[str, Any]] = [
    {
        "id": "prod_1",
        "sku": "TEA-GREEN-100",
        "name": "Green Tea 100g",
        "list_price_paise": 24900,
        "cost_paise": 12000,
        "quantity_on_hand": 40,
        "quantity_available": 40,
        "attributes": {},
        "active": True,
    },
    {
        "id": "prod_2",
        "sku": "TEA-BLACK-100",
        "name": "Black Tea 100g",
        "list_price_paise": 19900,
        "cost_paise": 9000,
        "quantity_on_hand": 12,
        "quantity_available": 12,
        "attributes": {},
        "active": True,
    },
    {
        "id": "prod_3",
        "sku": "TEA-RARE-500",
        "name": "Rare Reserve Tea 500g",
        "list_price_paise": 899000,
        "cost_paise": 400000,
        "quantity_on_hand": 2,
        "quantity_available": 2,
        "attributes": {},
        "active": True,
    },
]


class StubControlPlane:
    """A minimal Control Plane that records every request it is sent.

    The recording is the point. Asserting "the agent never called a payment
    endpoint" against a list of what was actually received is evidence;
    asserting it against the agent's own report would only prove the report
    agrees with itself.
    """

    def __init__(self, *, authorization_status: str = "approved") -> None:
        self.requests: list[tuple[str, str]] = []
        #: (path, parsed body) for every request that carried one, so a test
        #: can assert on what was actually sent rather than on what a helper
        #: says was sent.
        self.bodies: list[tuple[str, dict[str, Any]]] = []
        #: Request headers, in order, so a test can assert on correlation.
        self.headers: list[dict[str, str]] = []
        self.authorization_status = authorization_status
        self.carts: dict[str, list[dict[str, Any]]] = {}
        self._cart_seq = 0

    @property
    def paths(self) -> list[str]:
        return [path for _method, path in self.requests]

    def touched(self, fragment: str) -> bool:
        return any(fragment in path for path in self.paths)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        # Recorded from the raw wire path, not the decoded one. A decoded path
        # turns ``..%2F..%2Fpayments`` back into ``../../payments`` and would
        # make an escaped, harmless request look like a payment call - the
        # opposite of what the encoding achieved.
        self.requests.append((method, request.url.raw_path.decode().split("?", 1)[0]))
        self.headers.append(dict(request.headers))
        if request.content:
            try:
                self.bodies.append((path, json.loads(request.content)))
            except json.JSONDecodeError:  # pragma: no cover - stub never sends this
                pass

        if method == "GET" and path == "/api/v1/products":
            q = (request.url.params.get("q") or "").lower()
            items = [
                p
                for p in CATALOG_FIXTURE
                if not q or any(term in p["name"].lower() for term in q.split())
            ]
            return httpx.Response(
                200,
                json={"items": items, "total": len(items), "limit": 25, "offset": 0},
            )

        if method == "GET" and path.startswith("/api/v1/products/"):
            sku = path.rsplit("/", 1)[-1]
            for product in CATALOG_FIXTURE:
                if product["sku"] == sku:
                    return httpx.Response(200, json=product)
            return httpx.Response(
                404, json={"error": {"code": "PRODUCT_NOT_FOUND", "message": "Not found"}}
            )

        if method == "POST" and path == "/api/v1/carts":
            self._cart_seq += 1
            cart_id = f"cart_stub_{self._cart_seq}"
            self.carts[cart_id] = []
            return httpx.Response(200, json=self._cart_body(cart_id))

        if method == "POST" and path.endswith("/items"):
            cart_id = path.split("/")[4]
            body = json.loads(request.content or b"{}")
            product = next(
                (p for p in CATALOG_FIXTURE if p["sku"] == body.get("sku")), None
            )
            if product is None or cart_id not in self.carts:
                return httpx.Response(
                    404, json={"error": {"code": "NOT_FOUND", "message": "Not found"}}
                )
            self.carts[cart_id].append(
                {
                    "item_id": f"item_{len(self.carts[cart_id]) + 1}",
                    "sku": product["sku"],
                    "name": product["name"],
                    "unit_price_paise": product["list_price_paise"],
                    "quantity": body["quantity"],
                    "line_total_paise": product["list_price_paise"] * body["quantity"],
                }
            )
            return httpx.Response(200, json=self._cart_body(cart_id))

        if method == "POST" and path.endswith("/checkout"):
            cart_id = path.split("/")[4]
            lines = self.carts.get(cart_id, [])
            subtotal = sum(line["line_total_paise"] for line in lines)
            return httpx.Response(
                201,
                json={
                    "id": "ord_stub_1",
                    "cart_id": cart_id,
                    "merchant_id": "merchant_keen",
                    "status": "pending",
                    "currency": "INR",
                    "line_items": lines,
                    "subtotal_paise": subtotal,
                    "discount_amount_paise": 0,
                    "final_amount_paise": subtotal,
                },
            )

        if method == "POST" and path == "/api/v1/authorizations":
            body = json.loads(request.content or b"{}")
            return httpx.Response(
                201,
                json={
                    "id": "authz_stub_1",
                    "status": self.authorization_status,
                    "amount_paise": body.get("amount_paise"),
                    "subject_id": body.get("subject_id"),
                    "reasons": [] if self.authorization_status != "denied" else ["over limit"],
                },
            )

        if method == "GET" and path.startswith("/api/v1/authorizations/"):
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[-1],
                    "status": self.authorization_status,
                    "reasons": [],
                },
            )

        # Anything else - a payment route above all - is refused, and the
        # attempt is already recorded in ``self.requests``.
        return httpx.Response(
            404, json={"error": {"code": "NOT_FOUND", "message": f"no route {method} {path}"}}
        )

    def _cart_body(self, cart_id: str) -> dict[str, Any]:
        lines = self.carts[cart_id]
        return {
            "id": cart_id,
            "merchant_id": "merchant_keen",
            "status": "open",
            "items": lines,
            "subtotal_paise": sum(line["line_total_paise"] for line in lines),
            "item_count": sum(line["quantity"] for line in lines),
            "line_count": len(lines),
        }


@pytest.fixture
def control_plane() -> StubControlPlane:
    return StubControlPlane()


@pytest.fixture
def ai_settings():
    """Settings built directly, never from the cached env-backed singleton.

    ``get_ai_settings`` is lru_cached, so a test that mutated the environment
    would either see a stale object or leak its change into every later test.
    """
    from ai_runtime.config import AIRuntimeSettings

    return AIRuntimeSettings(
        control_plane_url="http://control-plane.test",
        agent_audience="keenpay-control-plane",
        max_tool_calls=12,
        max_recommendations=3,
        max_request_amount_paise=5_000_00,
    )
