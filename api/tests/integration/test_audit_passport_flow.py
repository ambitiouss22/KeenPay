"""Audit and passport endpoints through the app.

Two things are being checked here that a unit test cannot: that the routes are
actually mounted and reachable, and that they are scoped and gated — a shopper
must not be able to read a merchant's audit trail, and there must be no route
anywhere that writes or edits an entry.
"""

from __future__ import annotations

import pytest

from modules.audit.ledger import AuditLedger
from modules.payments.state import PaymentState
from repositories.payments import PaymentRepository

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"


@pytest.fixture
async def captured_payment() -> dict:
    """A settled payment with a short recorded history."""
    payments = PaymentRepository()
    ledger = AuditLedger()

    record = await payments.create(
        merchant_id=MERCHANT,
        order_id="ord_audit_flow",
        amount_paise=449800,
        idempotency_key="idem_audit_flow_key",
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
        order_snapshot_hash="d" * 64,
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
        record["id"], amount_paise=449800, provider_payment_id="pay_audit_flow"
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


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- audit ------------------------------------------------------------------


async def test_entries_are_listed_with_the_head_hash(client, admin_token, captured_payment):
    response = await client.get("/api/v1/audit/entries", headers=auth(admin_token))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["head_hash"] == body["entries"][-1]["entry_hash"]


async def test_entries_can_be_filtered(client, admin_token, captured_payment):
    response = await client.get(
        "/api/v1/audit/entries",
        params={"action": "PAYMENT_CAPTURED"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_the_chain_verifies_through_the_api(client, admin_token, captured_payment):
    response = await client.get("/api/v1/audit/verify", headers=auth(admin_token))

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["entry_count"] == 2


async def test_a_broken_chain_is_reported_as_invalid_not_as_an_error(
    client, admin_token, captured_payment
):
    """The request succeeded; it is the data that is wrong."""
    from modules.audit import ledger as ledger_module

    ledger_module._CHAINS[MERCHANT][0]["payload"]["amount_paise"] = 1

    response = await client.get("/api/v1/audit/verify", headers=auth(admin_token))

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["errors"]


async def test_a_shopper_cannot_read_the_audit_trail(client, shopper_token, captured_payment):
    response = await client.get("/api/v1/audit/entries", headers=auth(shopper_token))
    assert response.status_code == 403


async def test_the_audit_trail_needs_authentication(client, captured_payment):
    response = await client.get("/api/v1/audit/entries")
    assert response.status_code == 401


async def test_support_can_read_but_the_ledger_has_no_write_route(
    client, support_token, captured_payment
):
    """Append-only means there is nothing to call, not that calling it is refused."""
    assert (
        await client.get("/api/v1/audit/entries", headers=auth(support_token))
    ).status_code == 200

    for method, path in (
        ("post", "/api/v1/audit/entries"),
        ("delete", "/api/v1/audit/entries"),
        ("put", "/api/v1/audit/entries"),
    ):
        response = await getattr(client, method)(path, headers=auth(support_token))
        assert response.status_code == 405, f"{method.upper()} {path} should not exist"


# --- passport ---------------------------------------------------------------


async def test_a_passport_is_issued_and_verifies(client, admin_token, captured_payment):
    issued = await client.get(
        f"/api/v1/passport/{captured_payment['id']}", headers=auth(admin_token)
    )
    assert issued.status_code == 200
    passport = issued.json()
    assert passport["body"]["payment"]["status"] == PaymentState.CAPTURED.value

    verified = await client.post(
        "/api/v1/passport/verify", json=passport, headers=auth(admin_token)
    )
    assert verified.status_code == 200
    assert verified.json()["valid"] is True


async def test_a_tampered_passport_is_reported_invalid(client, admin_token, captured_payment):
    issued = await client.get(
        f"/api/v1/passport/{captured_payment['id']}", headers=auth(admin_token)
    )
    passport = issued.json()
    passport["body"]["payment"]["amount_paise"] = 1

    verified = await client.post(
        "/api/v1/passport/verify", json=passport, headers=auth(admin_token)
    )
    assert verified.status_code == 200
    assert verified.json()["valid"] is False
    assert verified.json()["errors"]


async def test_a_passport_for_an_unknown_payment_is_404(client, admin_token):
    response = await client.get("/api/v1/passport/pay_nope", headers=auth(admin_token))
    assert response.status_code == 404


async def test_the_verify_route_is_not_shadowed_by_the_id_route(client, admin_token):
    """``/verify`` must not be matched as a payment id."""
    response = await client.post(
        "/api/v1/passport/verify",
        json={"body": {}, "signature": {}},
        headers=auth(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False


async def test_a_shopper_cannot_pull_a_passport(client, shopper_token, captured_payment):
    response = await client.get(
        f"/api/v1/passport/{captured_payment['id']}", headers=auth(shopper_token)
    )
    assert response.status_code == 403


# --- reconciliation ---------------------------------------------------------


async def test_reconciliation_status_is_readable(client, admin_token):
    response = await client.get("/api/v1/reconciliation/status", headers=auth(admin_token))
    assert response.status_code == 200
    assert response.json()["unknown_payments"] == 0


async def test_a_reconciliation_run_reports_a_clean_pass(client, admin_token):
    response = await client.post("/api/v1/reconciliation/run", headers=auth(admin_token))
    assert response.status_code == 200
    assert response.json()["clean"] is True
    assert response.json()["checked"] == 0


async def test_support_may_read_but_not_start_a_run(client, support_token):
    assert (
        await client.get("/api/v1/reconciliation/status", headers=auth(support_token))
    ).status_code == 200
    assert (
        await client.post("/api/v1/reconciliation/run", headers=auth(support_token))
    ).status_code == 403
