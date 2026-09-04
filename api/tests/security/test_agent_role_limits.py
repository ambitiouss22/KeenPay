"""What an agent credential cannot do, proved against the real routes.

The AI Runtime's own tests show it never *tries* to move money. These show
that it would fail if it did - that the Control Plane refuses an agent
credential at the money routes regardless of what the runtime attempts.

Two independent guarantees, and the system needs both. A guard that exists
only in the caller is a guard that disappears the moment someone writes a
second caller.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.jwt import JWTManager
from repositories.carts import reset_carts

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

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
async def agent(client, admin):
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={"agent_id": "agent_buyer_1", "scopes": AGENT_SCOPES},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- the money routes ------------------------------------------------------


async def test_an_agent_cannot_create_a_payment(client, agent):
    response = await client.post(
        "/api/v1/payments",
        headers=agent,
        json={
            "order_id": "ord_x",
            "authorization_id": "authz_x",
            "idempotency_key": "a" * 20,
        },
    )
    assert response.status_code == 403


async def test_an_agent_cannot_refund_a_payment(client, agent):
    response = await client.post(
        "/api/v1/payments/pay_x/refund",
        headers=agent,
        json={"amount_paise": 100, "authorization_id": "authz_x", "idempotency_key": "a" * 20},
    )
    assert response.status_code == 403


async def test_an_agent_cannot_approve_its_own_request(client, agent, admin):
    """The separation of duties the whole gate rests on."""
    sku = f"S-{uuid.uuid4().hex[:8].upper()}"
    await client.post(
        "/api/v1/products",
        headers=admin,
        json={
            "sku": sku,
            "name": "Item",
            "list_price_paise": 500000,
            "cost_paise": 100000,
            "quantity_on_hand": 5,
        },
    )
    cart = await client.post("/api/v1/carts", headers=agent)
    cart_id = cart.json()["id"]
    await client.post(
        f"/api/v1/carts/{cart_id}/items", headers=agent, json={"sku": sku, "quantity": 1}
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
    assert created.status_code == 201, created.text

    blocked = await client.post(
        f"/api/v1/authorizations/{created.json()['id']}/approve",
        headers=agent,
        json={"note": "looks fine to me"},
    )
    assert blocked.status_code == 403


async def test_an_agent_cannot_mint_itself_a_wider_credential(client, agent):
    """Otherwise the least-privilege boundary lasts exactly one request."""
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=agent,
        json={"agent_id": "agent_2", "scopes": AGENT_SCOPES},
    )
    assert response.status_code == 403


async def test_an_agent_cannot_create_an_api_key(client, agent):
    response = await client.post(
        "/api/v1/auth/api-keys", headers=agent, json={"name": "escape hatch"}
    )
    assert response.status_code == 403


async def test_an_agent_cannot_write_to_the_catalogue(client, agent):
    """An agent that could set prices could set the price it pays."""
    response = await client.post(
        "/api/v1/products",
        headers=agent,
        json={
            "sku": "AGENT-OWNED",
            "name": "One rupee laptop",
            "list_price_paise": 100,
            "cost_paise": 0,
        },
    )
    assert response.status_code == 403


# --- forging -------------------------------------------------------------


async def test_a_forged_scope_claim_grants_nothing(client):
    """Scopes intersect the role. A scope the role lacks is inert.

    Signed here with the server's own test secret, which is the strongest
    version of this attack: the token is genuinely valid, and still achieves
    nothing beyond what role ``agent`` already permitted.
    """
    forged = JWTManager().create_access_token(
        user_id="agent_evil",
        merchant_id="merchant_keen",
        role="agent",
        audience="keenpay-control-plane",
        scopes=["authorization:approve", "admin:policy", "refund:request"],
    )
    headers = {"Authorization": f"Bearer {forged}"}

    blocked = await client.post(
        "/api/v1/authorizations/authz_any/approve", headers=headers, json={}
    )
    assert blocked.status_code == 403

    assert (
        await client.post(
            "/api/v1/products",
            headers=headers,
            json={"sku": "X", "name": "X", "list_price_paise": 1, "cost_paise": 0},
        )
    ).status_code == 403


async def test_a_credential_minted_for_another_service_is_refused(client):
    """Correctly signed, wrong audience. It must not be replayable here."""
    foreign = JWTManager().create_access_token(
        user_id="agent_1",
        merchant_id="merchant_keen",
        role="agent",
        audience="some-other-runtime",
        scopes=["catalog:read"],
    )
    response = await client.get(
        "/api/v1/products", headers={"Authorization": f"Bearer {foreign}"}
    )
    assert response.status_code == 401


async def test_an_expired_agent_credential_is_refused(client):
    from datetime import timedelta

    expired = JWTManager().create_access_token(
        user_id="agent_1",
        merchant_id="merchant_keen",
        role="agent",
        audience="keenpay-control-plane",
        scopes=["catalog:read"],
        expires_delta=timedelta(seconds=-1),
    )
    response = await client.get(
        "/api/v1/products", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
