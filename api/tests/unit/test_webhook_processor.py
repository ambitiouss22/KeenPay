"""What the webhook processor must do to an event before believing it.

The endpoint is unauthenticated by necessity, so these tests are written from
the attacker's side as much as the provider's: forge the signature, replay a
real event, settle a large order with a small payment, send an expiry after a
capture. Each one has to fail closed, and fail with a status code that makes
the *sender* do the right thing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from modules.audit.ledger import AuditLedger
from modules.webhooks.processor import (
    MAX_BODY_BYTES,
    MAX_CLOCK_SKEW_SECONDS,
    WebhookProcessor,
    WebhookVerdict,
    verify_signature,
)
from repositories.orders import OrderRepository
from repositories.webhooks import WebhookRepository

pytestmark = pytest.mark.asyncio

SECRET = "whsec_processor_tests"  # noqa: S105 - a test fixture, not a credential
MERCHANT = "merchant_keen"


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def paid_event(
    *,
    event_id: str = "evt_1",
    link_id: str = "plink_1",
    amount: int | None = 449800,
    payment_id: str = "pay_live_1",
    event: str = "payment_link.paid",
    created_at: int | None = None,
) -> bytes:
    payload: dict = {
        "entity": "event",
        "event": event,
        "event_id": event_id,
        "payload": {
            "payment_link": {"entity": {"id": link_id, "amount": amount}},
            "payment": {"entity": {"id": payment_id, "amount": amount}},
        },
    }
    if created_at is not None:
        payload["created_at"] = created_at
    return json.dumps(payload).encode()


@pytest.fixture
def orders() -> OrderRepository:
    return OrderRepository()


@pytest.fixture
def processor(orders) -> WebhookProcessor:
    return WebhookProcessor(
        orders=orders,
        events=WebhookRepository(),
        ledger=AuditLedger(),
        secret=SECRET,
    )


@pytest.fixture
async def order(orders) -> dict:
    return await orders.create_pending(
        session_id="11111111-1111-1111-1111-111111111111",
        merchant_id=MERCHANT,
        user_id="user_1",
        line_items=[{"sku": "A", "name": "A", "quantity": 1, "list_price_paise": 449800}],
        subtotal_paise=449800,
        discount_amount_paise=0,
        final_amount_paise=449800,
        guardrail_decision_id="22222222-2222-2222-2222-222222222222",
        offer_version=1,
        policy_version="v1",
        idempotency_key="idem_order_1",
        razorpay_payment_link_id="plink_1",
        razorpay_payment_link_url="https://rzp.io/mock/plink_1",
    )


# --- signature --------------------------------------------------------------


async def test_signature_helper_rejects_an_empty_secret():
    """No secret configured must never mean no checking."""
    body = b'{"event":"x"}'
    assert verify_signature(body, sign(body), "") is False


async def test_signature_helper_rejects_a_missing_signature():
    body = b'{"event":"x"}'
    assert verify_signature(body, None, SECRET) is False


async def test_signature_is_computed_over_the_raw_bytes():
    """Re-serialising changes the bytes, so the signature must stop matching."""
    original = b'{"b":1,"a":2}'
    reserialized = json.dumps(json.loads(original)).encode()
    assert original != reserialized
    assert verify_signature(reserialized, sign(original), SECRET) is False


async def test_a_forged_signature_is_rejected(processor):
    body = paid_event()
    outcome = await processor.handle(body, "deadbeef")
    assert outcome.verdict is WebhookVerdict.INVALID_SIGNATURE
    assert outcome.status_code == 401


async def test_a_rejected_event_is_never_recorded(processor):
    """A forged event must not consume the id a real one would need."""
    body = paid_event(event_id="evt_forged")
    await processor.handle(body, "deadbeef")
    assert await WebhookRepository().get("evt_forged") is None


# --- freshness --------------------------------------------------------------


async def test_a_replayed_old_event_is_rejected(processor):
    """A captured signature stays valid forever; freshness is what stops replay."""
    stale = int(time.time()) - (MAX_CLOCK_SKEW_SECONDS + 60)
    body = paid_event(created_at=stale)
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.STALE
    assert outcome.status_code == 400


async def test_a_fresh_event_passes_the_skew_check(processor, order):
    body = paid_event(created_at=int(time.time()))
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.PROCESSED


async def test_an_event_with_no_timestamp_is_not_rejected(processor, order):
    """The provider does not always send one; refusing those drops real traffic."""
    body = paid_event()
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.PROCESSED


# --- deduplication ----------------------------------------------------------


async def test_a_duplicate_delivery_is_ignored(processor, order, orders):
    body = paid_event(event_id="evt_dupe")

    first = await processor.handle(body, sign(body))
    second = await processor.handle(body, sign(body))

    assert first.verdict is WebhookVerdict.PROCESSED
    assert second.verdict is WebhookVerdict.DUPLICATE
    # 200: the provider has delivered it, and retrying will not help.
    assert second.status_code == 200


async def test_a_duplicate_does_not_write_a_second_ledger_entry(processor, order):
    ledger = AuditLedger()
    body = paid_event(event_id="evt_dupe_ledger")

    await processor.handle(body, sign(body))
    await processor.handle(body, sign(body))

    entries, total = await ledger.entries_for(
        MERCHANT, action="PAYMENT_CAPTURED", limit=100
    )
    assert total == 1, [e.action for e in entries]


async def test_an_event_with_no_id_is_refused(processor):
    """An event that cannot be deduplicated would be applied on every retry."""
    body = json.dumps({"event": "payment_link.paid", "payload": {}}).encode()
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.MALFORMED


async def test_the_header_event_id_is_preferred(processor, order):
    body = paid_event(event_id="evt_body")
    await processor.handle(body, sign(body), header_event_id="evt_header")

    assert await WebhookRepository().get("evt_header") is not None
    assert await WebhookRepository().get("evt_body") is None


# --- amount matching --------------------------------------------------------


async def test_a_short_payment_does_not_settle_the_order(processor, order, orders):
    """The event says what was paid; the order says what was owed."""
    body = paid_event(event_id="evt_short", amount=100)
    outcome = await processor.handle(body, sign(body))

    assert outcome.verdict is WebhookVerdict.AMOUNT_MISMATCH
    assert outcome.status_code == 409

    stored = await orders.get(order["id"])
    assert stored["status"] == "payment_disputed"
    assert stored["status"] != "paid"


async def test_an_overpayment_is_also_a_mismatch(processor, order, orders):
    body = paid_event(event_id="evt_over", amount=449801)
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.AMOUNT_MISMATCH


async def test_an_event_with_no_amount_is_a_mismatch(processor, order, orders):
    """Absent is not zero and it is certainly not 'the right amount'."""
    body = paid_event(event_id="evt_no_amount", amount=None)
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.AMOUNT_MISMATCH


async def test_a_mismatch_is_written_to_the_ledger(processor, order):
    body = paid_event(event_id="evt_mismatch_ledger", amount=1)
    await processor.handle(body, sign(body))

    entries, total = await AuditLedger().entries_for(
        MERCHANT, action="PAYMENT_DISPUTED", limit=10
    )
    assert total == 1
    assert entries[0].payload["expected_paise"] == 449800
    assert entries[0].payload["received_paise"] == 1


async def test_an_exact_payment_settles_the_order(processor, order, orders):
    body = paid_event(event_id="evt_exact")
    outcome = await processor.handle(body, sign(body))

    assert outcome.verdict is WebhookVerdict.PROCESSED
    stored = await orders.get(order["id"])
    assert stored["status"] == "paid"
    assert stored["razorpay_payment_id"] == "pay_live_1"


# --- other events -----------------------------------------------------------


async def test_an_unknown_event_type_is_acknowledged_and_ignored(processor):
    body = json.dumps({"event": "subscription.charged", "event_id": "evt_sub"}).encode()
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.IGNORED
    assert outcome.status_code == 200


async def test_payment_captured_settles_the_order_too(processor, order, orders):
    body = paid_event(event_id="evt_captured", event="payment.captured")
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.PROCESSED
    assert (await orders.get(order["id"]))["status"] == "paid"


async def test_an_expiry_moves_an_unpaid_order_to_expired(processor, order, orders):
    body = json.dumps(
        {
            "event": "payment_link.expired",
            "event_id": "evt_expired",
            "payload": {"payment_link": {"entity": {"id": "plink_1"}}},
        }
    ).encode()
    outcome = await processor.handle(body, sign(body))

    assert outcome.verdict is WebhookVerdict.PROCESSED
    assert (await orders.get(order["id"]))["status"] == "expired"


async def test_an_expiry_after_payment_does_not_unpay_the_order(processor, order, orders):
    """Provider events arrive out of order. A late expiry is not a reversal."""
    paid = paid_event(event_id="evt_paid_first")
    await processor.handle(paid, sign(paid))

    expiry = json.dumps(
        {
            "event": "payment_link.expired",
            "event_id": "evt_expired_late",
            "payload": {"payment_link": {"entity": {"id": "plink_1"}}},
        }
    ).encode()
    outcome = await processor.handle(expiry, sign(expiry))

    assert outcome.verdict is WebhookVerdict.IGNORED
    assert (await orders.get(order["id"]))["status"] == "paid"


async def test_a_failed_attempt_leaves_the_order_pending(processor, order, orders):
    """A shopper can retry on the same link; the sale is still live."""
    body = json.dumps(
        {
            "event": "payment.failed",
            "event_id": "evt_failed",
            "payload": {
                "payment_link": {"entity": {"id": "plink_1"}},
                "payment": {"entity": {"id": "pay_x", "error_code": "BAD_CARD"}},
            },
        }
    ).encode()
    outcome = await processor.handle(body, sign(body))

    assert outcome.verdict is WebhookVerdict.PROCESSED
    assert (await orders.get(order["id"]))["status"] == "pending"


async def test_an_event_for_an_unknown_order_is_acknowledged(processor):
    """Signed, well formed, and not ours. Retrying will not produce an order."""
    body = paid_event(event_id="evt_orphan", link_id="plink_not_ours")
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.ORDER_NOT_FOUND
    assert outcome.status_code == 200


# --- malformed input --------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [b"", b"garbage", b"null", b"[1,2,3]", b'"a string"', b"123", b"{", b"\xff\xfe"]
)
async def test_malformed_bodies_are_rejected_without_raising(processor, raw):
    outcome = await processor.handle(raw, sign(raw))
    assert outcome.verdict is WebhookVerdict.MALFORMED
    assert outcome.status_code == 400


async def test_a_nested_field_of_the_wrong_type_does_not_raise(processor):
    """None of the payload is ours, so no level of it can be assumed to be an object."""
    body = json.dumps(
        {
            "event": "payment_link.paid",
            "event_id": "evt_wrong_types",
            "payload": {"payment_link": "not-an-object", "payment": ["also", "not"]},
        }
    ).encode()
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.ORDER_NOT_FOUND


async def test_an_oversized_body_is_refused_unread(processor):
    raw = b"x" * (MAX_BODY_BYTES + 1)
    outcome = await processor.handle(raw, sign(raw))
    assert outcome.verdict is WebhookVerdict.TOO_LARGE
    assert outcome.status_code == 413


async def test_a_non_integer_amount_is_not_treated_as_a_number(processor, order):
    """A string amount must not compare equal to, or coerce into, the total owed."""
    body = json.dumps(
        {
            "event": "payment_link.paid",
            "event_id": "evt_string_amount",
            "payload": {
                "payment_link": {"entity": {"id": "plink_1", "amount": "449800"}},
                "payment": {"entity": {"id": "pay_x", "amount": "449800"}},
            },
        }
    ).encode()
    outcome = await processor.handle(body, sign(body))
    assert outcome.verdict is WebhookVerdict.AMOUNT_MISMATCH


# --- response shape ---------------------------------------------------------


async def test_an_accepted_event_answers_with_an_acknowledgement(processor, order):
    body = paid_event(event_id="evt_ack")
    outcome = await processor.handle(body, sign(body))
    assert outcome.body()["received"] is True
    assert outcome.body()["order_id"] == order["id"]


async def test_a_rejected_event_answers_with_the_standard_error_envelope(processor):
    outcome = await processor.handle(paid_event(), "nope")
    assert outcome.body()["error"]["code"] == "INVALID_SIGNATURE"
