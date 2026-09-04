"""Unit-test fixtures for the payment stores."""

import pytest

from repositories.idempotency import reset_idempotency
from repositories.outbox import reset_outbox
from repositories.payments import reset_payments


@pytest.fixture(autouse=True)
def _reset_payment_stores():
    """The payment stores are module-level dicts.

    Without this, tests see each other's records and pass or fail depending on
    the order they happen to run in.
    """
    reset_payments()
    reset_idempotency()
    reset_outbox()
    yield
    reset_payments()
    reset_idempotency()
    reset_outbox()


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
