"""Minting an agent credential, and shopping the real API with it.

This is the join between the two services. Everything else about the AI
Runtime is tested against a stub Control Plane; here a credential the real
Control Plane issued is used against the real Control Plane's routes, so the
scopes, the role and the audience are proved to line up end to end.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from repositories.carts import reset_carts

pytestmark = pytest.mark.asyncio

PASSWORD = "KeenPayDev1!"
AGENT_SCOPES = [
    "catalog:read",
    "session:create",
    "order:read:own",
    "authorization:request",
    "authorization:read",
]


@pytest.fixture(autouse=True)
def _clean_carts():
    reset_carts()
    yield
    reset_carts()


@pytest.fixture
def app():
    from main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _login(client, email):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "merchant_id": "merchant_keen"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def admin(client):
    return await _login(client, "admin@keenpay.dev")


@pytest.fixture
async def shopper(client):
    return await _login(client, "shopper@keenpay.dev")


async def _mint(client, admin, *, scopes=None, **extra):
    return await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={"agent_id": "agent_buyer_1", "scopes": scopes or AGENT_SCOPES, **extra},
    )


@pytest.fixture
async def agent(client, admin):
    response = await _mint(client, admin)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def product(client, admin):
    sku = f"A-{uuid.uuid4().hex[:8].upper()}"
    response = await client.post(
        "/api/v1/products",
        headers=admin,
        json={
            "sku": sku,
            "name": "Agent Test Item",
            "list_price_paise": 19900,
            "cost_paise": 9000,
            "quantity_on_hand": 25,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- issuing ---------------------------------------------------------------


async def test_an_admin_can_mint_an_agent_credential(client, admin):
    response = await _mint(client, admin)
    body = response.json()

    assert response.status_code == 201
    assert body["role"] == "agent"
    assert body["audience"] == "keenpay-control-plane"
    assert body["merchant_id"] == "merchant_keen"
    assert sorted(body["scopes"]) == sorted(AGENT_SCOPES)
    assert 0 < body["expires_in"] <= 3600


async def test_the_response_echoes_what_was_granted_not_what_was_asked(client, admin):
    response = await _mint(client, admin, scopes=["catalog:read", "catalog:read"])
    assert response.json()["scopes"] == ["catalog:read"]


async def test_a_shopper_cannot_mint_one(client, shopper):
    """Minting a machine credential is an administrative act."""
    response = await _mint(client, shopper)
    assert response.status_code == 403


async def test_minting_requires_authentication(client):
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        json={"agent_id": "a", "scopes": ["catalog:read"]},
    )
    assert response.status_code == 401


async def test_the_body_cannot_name_its_own_merchant(client, admin):
    """Otherwise an operator could mint a credential for someone else's shop."""
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={
            "agent_id": "a",
            "scopes": ["catalog:read"],
            "merchant_id": "merchant_other",
        },
    )
    assert response.status_code == 422


async def test_the_body_cannot_name_its_own_role(client, admin):
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={"agent_id": "a", "scopes": ["catalog:read"], "role": "admin"},
    )
    assert response.status_code == 422


async def test_an_ungrantable_scope_is_a_400_naming_it(client, admin):
    response = await _mint(client, admin, scopes=["authorization:approve"])
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_AGENT_SCOPE"
    assert "authorization:approve" in body["error"]["message"]


async def test_an_oversized_ttl_is_refused_at_the_edge(client, admin):
    response = await _mint(client, admin, ttl_seconds=99999)
    assert response.status_code == 422


# --- using it --------------------------------------------------------------


async def test_the_agent_can_complete_the_whole_shopping_path(client, agent, product):
    """Catalogue, cart, order, authorization request - and it stops there."""
    listed = await client.get("/api/v1/products", headers=agent)
    assert listed.status_code == 200

    cart = await client.post("/api/v1/carts", headers=agent)
    assert cart.status_code == 201, cart.text
    cart_id = cart.json()["id"]

    added = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=agent,
        json={"sku": product["sku"], "quantity": 2},
    )
    assert added.status_code == 200, added.text

    order = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=agent,
        json={"idempotency_key": f"agent-{uuid.uuid4().hex}"},
    )
    assert order.status_code == 201, order.text
    total = order.json()["final_amount_paise"]
    assert total == product["list_price_paise"] * 2

    authorization = await client.post(
        "/api/v1/authorizations",
        headers=agent,
        json={
            "kind": "payment",
            "amount_paise": total,
            "subject_id": order.json()["id"],
        },
    )
    assert authorization.status_code == 201, authorization.text
    assert authorization.json()["status"] in {"approved", "pending", "denied"}


async def test_the_agent_can_read_back_what_it_requested(client, agent, product):
    cart = await client.post("/api/v1/carts", headers=agent)
    cart_id = cart.json()["id"]
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=agent,
        json={"sku": product["sku"], "quantity": 1},
    )
    order = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=agent,
        json={"idempotency_key": f"agent-{uuid.uuid4().hex}"},
    )
    created = await client.post(
        "/api/v1/authorizations",
        headers=agent,
        json={
            "kind": "payment",
            "amount_paise": order.json()["final_amount_paise"],
            "subject_id": order.json()["id"],
        },
    )
    read_back = await client.get(
        f"/api/v1/authorizations/{created.json()['id']}", headers=agent
    )
    assert read_back.status_code == 200
    assert read_back.json()["id"] == created.json()["id"]


# --- scope narrowing over HTTP --------------------------------------------


async def test_a_catalogue_only_credential_cannot_open_a_cart(client, admin):
    """Least privilege, observed through the real routes."""
    response = await _mint(client, admin, scopes=["catalog:read"])
    narrow = {"Authorization": f"Bearer {response.json()['access_token']}"}

    assert (await client.get("/api/v1/products", headers=narrow)).status_code == 200

    blocked = await client.post("/api/v1/carts", headers=narrow)
    assert blocked.status_code == 403
    assert "scope" in blocked.json()["error"]["message"].lower()


async def test_a_credential_without_the_request_scope_cannot_ask_for_money(
    client, admin, product
):
    response = await _mint(client, admin, scopes=["catalog:read", "session:create"])
    narrow = {"Authorization": f"Bearer {response.json()['access_token']}"}

    cart = await client.post("/api/v1/carts", headers=narrow)
    assert cart.status_code == 201

    blocked = await client.post(
        "/api/v1/authorizations",
        headers=narrow,
        json={"kind": "payment", "amount_paise": 100, "subject_id": "ord_x"},
    )
    assert blocked.status_code == 403
