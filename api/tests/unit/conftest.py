"""Unit-test fixtures for the payment, event and ledger stores."""

import pytest

from modules.audit.ledger import reset_ledger
from repositories.idempotency import reset_idempotency
from repositories.orders import reset_orders
from repositories.outbox import reset_outbox
from repositories.payments import reset_payments
from repositories.reconciliation import reset_reconciliation
from repositories.webhooks import reset_webhooks


@pytest.fixture(autouse=True)
def _reset_payment_stores():
    """The stores are module-level dicts.

    Without this, tests see each other's records and pass or fail depending on
    the order they happen to run in. The audit ledger matters most: it is a
    hash chain, so a single leaked entry changes every hash a later test
    computes, and the failure looks like a bug in the hashing rather than in
    the fixtures.
    """
    reset_payments()
    reset_idempotency()
    reset_outbox()
    reset_orders()
    reset_webhooks()
    reset_reconciliation()
    reset_ledger()
    yield
    reset_payments()
    reset_idempotency()
    reset_outbox()
    reset_orders()
    reset_webhooks()
    reset_reconciliation()
    reset_ledger()


@pytest.fixture
def mock_order():
    """A fully specified order, the shape the snapshot requires."""
    return {
        "id": "ord_test123",
        "merchant_id": "merchant_1",
        "user_id": "user_1",
        "status": "pending",
        "line_items": [
            {
                "sku": "TEST",
                "name": "Test Item",
                "quantity": 1,
                "list_price_paise": 10000,
                "cost_paise": 5000,
            }
        ],
        "subtotal_paise": 10000,
        "discount_amount_paise": 0,
        "final_amount_paise": 10000,
        "currency": "INR",
    }
