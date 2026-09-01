"""End-to-end checkout flow integration test."""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_checkout_flow(client: AsyncClient, shopper_token: str):
    headers = {"Authorization": f"Bearer {shopper_token}"}

    session_resp = await client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"merchant_id": "merchant_keen"},
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    catalog = await client.get("/api/v1/catalog/products?q=hoodie", headers=headers)
    assert catalog.status_code == 200
    assert catalog.json()["total"] >= 1

    chat = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        headers=headers,
        json={"text": "2 navy hoodies medium best price"},
    )
    assert chat.status_code == 200
    assert chat.json()["role"] == "assistant"

    confirm = await client.post(
        f"/api/v1/sessions/{session_id}/confirm",
        headers=headers,
        json={"confirmed": True, "idempotency_key": f"confirm-{session_id}-v1"},
    )
    assert confirm.status_code == 200
    body = confirm.json()
    assert body["payment_link_url"].startswith("https://rzp.io/")
    assert body["final_amount_paise"] > 0

    order = await client.get(f"/api/v1/orders/{body['order_id']}", headers=headers)
    assert order.status_code == 200
    assert order.json()["status"] == "pending"
