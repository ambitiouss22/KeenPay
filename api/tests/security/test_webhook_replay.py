"""Replaying a genuine event must not move money twice.

A webhook signature is a proof of origin, not a proof of freshness. Anyone who
observes one valid delivery holds a payload and a signature that will verify
forever, so the only things standing between that capture and a second
settlement are the event id claim and the timestamp window. These tests attack
both, plus the amount check that stops a small forged payment from closing a
large order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from modules.audit.ledger import AuditLedger
from modules.webhooks.processor import MAX_CLOCK_SKEW_SECONDS
from repositories.orders import OrderRepository

pytestmark = pytest.mark.asyncio

SECRET = "whsec_replay_tests"  # noqa: S105
MERCHANT = "merchant_keen"
LINK_ID = "plink_replay_1"
AMOUNT = 449800


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def event(
    event_id: str = "evt_replay_1",
    *,
    amount: int = AMOUNT,
    created_at: int | None = None,
) -> bytes:
    payload: dict = {
        "entity": "event",
        "event": "payment_link.paid",
        "event_id": event_id,
        "payload": {
            "payment_link": {"entity": {"id": LINK_ID, "amount": amount}},
            "payment": {"entity": {"id": "pay_replay_1", "amount": amount}},
        },
    }
    if created_at is not None:
        payload["created_at"] = created_at
    return json.dumps(payload).encode()


@pytest.fixture
def live_app(monkeypatch):
    """The app with signature verification actually switched on."""
    monkeypatch.setenv("RAZORPAY_MOCK", "false")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)

    from config.settings import get_settings

    get_settings.cache_clear()
    from main import create_app

    app = create_app()
    yield app
    get_settings.cache_clear()


@pytest.fixture
async def live(live_app):
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def order() -> dict:
    return await OrderRepository().create_pending(
        session_id="55555555-5555-5555-5555-555555555555",
        merchant_id=MERCHANT,
        user_id="user_dev_shopper",
        line_items=[{"sku": "A", "name": "A", "quantity": 1, "list_price_paise": AMOUNT}],
        subtotal_paise=AMOUNT,
        discount_amount_paise=0,
        final_amount_paise=AMOUNT,
        guardrail_decision_id="66666666-6666-6666-6666-666666666666",
        offer_version=1,
        policy_version="v1",
        idempotency_key="idem_replay_order",
        razorpay_payment_link_id=LINK_ID,
        razorpay_payment_link_url=f"https://rzp.io/mock/{LINK_ID}",
    )


# --- replay -----------------------------------------------------------------


async def test_replaying_a_captured_delivery_settles_nothing_twice(live, order):
    body = event("evt_replay_once")
    signature = sign(body)

    first = await live.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": signature}
    )
    replays = [
        await live.post(
            "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": signature}
        )
        for _ in range(5)
    ]

    assert first.json()["status"] == "processed"
    assert all(r.json()["status"] == "duplicate" for r in replays)

    entries, total = await AuditLedger().entries_for(MERCHANT, action="PAYMENT_CAPTURED")
    assert total == 1, [e.action for e in entries]


async def test_an_old_capture_replayed_later_is_refused_on_freshness(live, order):
    body = event("evt_replay_stale", created_at=int(time.time()) - MAX_CLOCK_SKEW_SECONDS - 60)

    response = await live.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sign(body)}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_STALE"
    assert (await OrderRepository().get(order["id"]))["status"] == "pending"


async def test_a_new_event_id_on_an_old_body_does_not_re_settle(live, order):
    """Changing the id changes the bytes, so the captured signature stops matching."""
    original = event("evt_replay_original")
    await live.post(
        "/webhooks/razorpay",
        content=original,
        headers={"X-Razorpay-Signature": sign(original)},
    )

    forged = event("evt_replay_forged")
    response = await live.post(
        "/webhooks/razorpay",
        content=forged,
        headers={"X-Razorpay-Signature": sign(original)},
    )

    assert response.status_code == 401


# --- amount tampering -------------------------------------------------------


async def test_a_cheap_forged_payment_cannot_close_an_expensive_order(live, order):
    body = event("evt_replay_cheap", amount=100)

    response = await live.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sign(body)}
    )

    assert response.status_code == 409
    stored = await OrderRepository().get(order["id"])
    assert stored["status"] == "payment_disputed"
    assert stored["razorpay_payment_id"] is None


async def test_an_event_signed_with_a_stolen_looking_secret_is_refused(live, order):
    body = event("evt_replay_wrong_secret")
    response = await live.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body, "attacker-secret")},
    )
    assert response.status_code == 401
    assert (await OrderRepository().get(order["id"]))["status"] == "pending"


async def test_an_unsigned_event_never_reaches_the_handler(live, order):
    response = await live.post("/webhooks/razorpay", content=event("evt_replay_unsigned"))
    assert response.status_code == 401
    assert (await OrderRepository().get(order["id"]))["status"] == "pending"

    entries, total = await AuditLedger().entries_for(MERCHANT)
    assert total == 0, "an unverified event must leave no trace in the ledger"
