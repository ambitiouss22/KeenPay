"""The webhook endpoint, exercised through the app the way the provider hits it.

The unit tests own the rules; these own the wiring — that the route reads the
raw body, that the status codes reach the client intact, and that the error
envelope is the same one the rest of the API produces.
"""

from __future__ import annotations

import json

import pytest

from modules.audit.ledger import AuditLedger
from repositories.orders import OrderRepository

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"


@pytest.fixture
async def order() -> dict:
    return await OrderRepository().create_pending(
        session_id="33333333-3333-3333-3333-333333333333",
        merchant_id=MERCHANT,
        user_id="user_dev_shopper",
        line_items=[{"sku": "A", "name": "A", "quantity": 1, "list_price_paise": 449800}],
        subtotal_paise=449800,
        discount_amount_paise=0,
        final_amount_paise=449800,
        guardrail_decision_id="44444444-4444-4444-4444-444444444444",
        offer_version=1,
        policy_version="v1",
        idempotency_key="idem_webhook_flow",
        razorpay_payment_link_id="plink_flow_1",
        razorpay_payment_link_url="https://rzp.io/mock/plink_flow_1",
    )


def event(event_id: str, amount: int = 449800, link_id: str = "plink_flow_1") -> bytes:
    return json.dumps(
        {
            "entity": "event",
            "event": "payment_link.paid",
            "event_id": event_id,
            "payload": {
                "payment_link": {"entity": {"id": link_id, "amount": amount}},
                "payment": {"entity": {"id": "pay_flow_1", "amount": amount}},
            },
        }
    ).encode()


async def test_a_paid_event_settles_the_order(client, order):
    response = await client.post("/webhooks/razorpay", content=event("evt_flow_1"))

    assert response.status_code == 200
    assert response.json()["received"] is True

    stored = await OrderRepository().get(order["id"])
    assert stored["status"] == "paid"


async def test_a_redelivery_is_answered_200_and_changes_nothing(client, order):
    body = event("evt_flow_dupe")

    first = await client.post("/webhooks/razorpay", content=body)
    second = await client.post("/webhooks/razorpay", content=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    entries, total = await AuditLedger().entries_for(MERCHANT, action="PAYMENT_CAPTURED")
    assert total == 1, [e.action for e in entries]


async def test_an_amount_mismatch_is_a_409_in_the_standard_envelope(client, order):
    response = await client.post("/webhooks/razorpay", content=event("evt_flow_short", 100))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WEBHOOK_AMOUNT_MISMATCH"

    stored = await OrderRepository().get(order["id"])
    assert stored["status"] == "payment_disputed"


async def test_a_malformed_body_is_a_400_not_a_500(client):
    response = await client.post("/webhooks/razorpay", content=b"not json at all")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAYLOAD"


async def test_an_event_for_an_unknown_link_is_acknowledged(client):
    response = await client.post(
        "/webhooks/razorpay", content=event("evt_flow_orphan", link_id="plink_nope")
    )
    assert response.status_code == 200
    assert response.json()["status"] == "order_not_found"


async def test_the_header_event_id_deduplicates(client, order):
    """Two bodies, one delivery id: the second must not settle anything again."""
    first = await client.post(
        "/webhooks/razorpay",
        content=event("evt_body_a"),
        headers={"X-Razorpay-Event-Id": "evt_delivery_1"},
    )
    second = await client.post(
        "/webhooks/razorpay",
        content=event("evt_body_b"),
        headers={"X-Razorpay-Event-Id": "evt_delivery_1"},
    )

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"


async def test_the_webhook_needs_no_authentication(client, order):
    """The provider has no token. Requiring one would drop every real event."""
    response = await client.post("/webhooks/razorpay", content=event("evt_flow_anon"))
    assert response.status_code == 200
