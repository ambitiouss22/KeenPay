"""A passport is only worth anything if a forged one fails to verify.

So the tests here are mostly forgeries: change the amount, swap the payment id,
re-sign with the wrong key, splice the audit chain, downgrade the algorithm.
Each has to be caught by :func:`verify_passport` alone — no database, no
application state — because "verifies offline" is the property being sold.
"""

from __future__ import annotations

import copy

import pytest

from modules.audit.ledger import AuditLedger
from modules.passport.service import (
    PASSPORT_VERSION,
    PassportService,
    sign_body,
    verify_passport,
)
from repositories.orders import OrderRepository
from repositories.payments import PaymentRepository
from repositories.webhooks import WebhookRepository

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"
KEY = "passport-signing-key-for-tests-only"  # noqa: S105
WRONG_KEY = "some-other-key-entirely"  # noqa: S105


@pytest.fixture
def ledger() -> AuditLedger:
    return AuditLedger()


@pytest.fixture
def payments() -> PaymentRepository:
    return PaymentRepository()


@pytest.fixture
def orders() -> OrderRepository:
    return OrderRepository()


@pytest.fixture
def service(payments, orders, ledger) -> PassportService:
    return PassportService(
        payments=payments,
        orders=orders,
        events=WebhookRepository(),
        ledger=ledger,
        signing_key=KEY,
    )


@pytest.fixture
async def payment(payments, ledger) -> dict:
    """A captured payment with a short history behind it."""
    record = await payments.create(
        merchant_id=MERCHANT,
        order_id="ord_passport_1",
        amount_paise=449800,
        idempotency_key="idem_passport_1",
        order_snapshot={
            "line_items": [
                {
                    "sku": "A",
                    "name": "Item A",
                    "quantity": 1,
                    "list_price_paise": 449800,
                    "cost_paise": 200000,
                }
            ],
            "subtotal_paise": 449800,
            "discount_amount_paise": 0,
            "final_amount_paise": 449800,
            "currency": "INR",
        },
        order_snapshot_hash="c" * 64,
    )
    await ledger.append(
        merchant_id=MERCHANT,
        entity_type="payment",
        entity_id=record["id"],
        actor="payment_engine",
        action="PAYMENT_CREATED",
        payload={"amount_paise": 449800},
    )
    await payments.mark_captured(
        record["id"], amount_paise=449800, provider_payment_id="pay_provider_1"
    )
    await ledger.append(
        merchant_id=MERCHANT,
        entity_type="payment",
        entity_id=record["id"],
        actor="payment_engine",
        action="PAYMENT_CAPTURED",
        payload={"amount_paise": 449800},
    )
    return await payments.get(record["id"], merchant_id=MERCHANT)


# --- issuing ----------------------------------------------------------------


async def test_a_passport_is_issued_for_a_known_payment(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)

    assert passport is not None
    assert passport["body"]["version"] == PASSPORT_VERSION
    assert passport["body"]["payment"]["id"] == payment["id"]
    assert passport["body"]["payment"]["amount_paise"] == 449800
    assert passport["signature"]["algorithm"] == "HMAC-SHA256"


async def test_a_passport_carries_the_audit_chain(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    ledger_section = passport["body"]["ledger"]

    assert ledger_section["entry_count"] == 2
    assert [e["action"] for e in ledger_section["entries"]] == [
        "PAYMENT_CREATED",
        "PAYMENT_CAPTURED",
    ]
    assert ledger_section["head_hash"] == ledger_section["entries"][-1]["entry_hash"]


async def test_a_passport_carries_the_snapshot_not_the_live_order(service, payment):
    """It must describe what was bought when money moved, not what it looks like now."""
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    snapshot = passport["body"]["order"]["snapshot"]

    assert snapshot["final_amount_paise"] == 449800
    assert snapshot["line_items"][0]["sku"] == "A"


async def test_another_merchant_gets_nothing(service, payment):
    """Not-yours and not-found answer the same, so an id cannot be confirmed."""
    assert await service.issue(payment["id"], merchant_id="merchant_rival") is None


async def test_an_unknown_payment_gets_nothing(service):
    assert await service.issue("pay_does_not_exist", merchant_id=MERCHANT) is None


# --- verification -----------------------------------------------------------


async def test_an_untouched_passport_verifies(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    valid, errors = verify_passport(passport, KEY)
    assert valid, errors


async def test_verification_needs_no_application_state(service, payment):
    """The offline claim: a plain dict and a key, nothing else."""
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    copied = copy.deepcopy(passport)

    valid, errors = verify_passport(copied, KEY)
    assert valid, errors


async def test_changing_the_amount_breaks_the_signature(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["body"]["payment"]["amount_paise"] = 1

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any("digest" in e or "Signature" in e for e in errors)


async def test_changing_the_payment_id_breaks_the_signature(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["body"]["payment"]["id"] = "pay_someone_elses"

    valid, _ = verify_passport(passport, KEY)
    assert not valid


async def test_a_forger_who_also_updates_the_digest_still_fails(service, payment):
    """Recomputing the hash is not enough; the HMAC needs the key."""
    from modules.passport.service import body_digest

    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["body"]["payment"]["amount_paise"] = 1
    passport["signature"]["body_hash"] = body_digest(passport["body"])

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any("Signature does not match" in e for e in errors)


async def test_a_passport_signed_with_the_wrong_key_fails(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["signature"]["value"] = sign_body(passport["body"], WRONG_KEY)

    valid, _ = verify_passport(passport, KEY)
    assert not valid


async def test_verifying_with_the_wrong_key_fails(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    valid, _ = verify_passport(passport, WRONG_KEY)
    assert not valid


async def test_an_empty_key_never_verifies(service, payment):
    """A missing key must refuse, not wave everything through."""
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    valid, errors = verify_passport(passport, "")
    assert not valid
    assert any("key" in e for e in errors)


async def test_a_downgraded_algorithm_is_refused(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["signature"]["algorithm"] = "none"

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any("algorithm" in e for e in errors)


async def test_an_unknown_version_is_refused(service, payment):
    """A verifier that guesses at a field's meaning accepts the wrong amount."""
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["body"]["version"] = "99"

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any("version" in e for e in errors)


async def test_an_edited_audit_entry_is_caught_by_the_chain_not_the_signature(
    service, payment
):
    """Two independent checks, and this test isolates the second one.

    The document is re-signed so the signature is above suspicion. What is left
    to catch the edit is the entry's own hash, which was written before the
    passport existed — so tampering with history *before* issue is detectable
    even by whoever holds the signing key.
    """
    from modules.passport.service import body_digest

    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    passport["body"]["ledger"]["entries"][0]["payload"]["amount_paise"] = 1
    passport["signature"]["body_hash"] = body_digest(passport["body"])
    passport["signature"]["value"] = sign_body(passport["body"], KEY)

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any(e.startswith("audit chain:") for e in errors)


async def test_dropping_an_audit_entry_breaks_the_signature(service, payment):
    """Pruning is what the signature covers; the chain covers editing."""
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    del passport["body"]["ledger"]["entries"][0]

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any("Signature" in e or "digest" in e for e in errors)


async def test_reordering_audit_entries_is_detected(service, payment):
    from modules.passport.service import body_digest

    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    entries = passport["body"]["ledger"]["entries"]
    entries[0], entries[1] = entries[1], entries[0]
    passport["signature"]["body_hash"] = body_digest(passport["body"])
    passport["signature"]["value"] = sign_body(passport["body"], KEY)

    valid, errors = verify_passport(passport, KEY)
    assert not valid
    assert any(e.startswith("audit chain:") for e in errors)


async def test_a_passport_missing_its_signature_is_refused():
    valid, errors = verify_passport({"body": {}}, KEY)
    assert not valid
    assert errors


async def test_the_service_verifies_with_its_own_key(service, payment):
    passport = await service.issue(payment["id"], merchant_id=MERCHANT)
    valid, errors = service.verify(passport)
    assert valid, errors
