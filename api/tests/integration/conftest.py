"""Integration-test fixtures for the payment stores."""

import pytest

from repositories.idempotency import reset_idempotency
from repositories.outbox import reset_outbox
from repositories.payments import reset_payments


@pytest.fixture(autouse=True)
def _reset_payment_stores():
    """Keep payment state from leaking between integration tests."""
    reset_payments()
    reset_idempotency()
    reset_outbox()
    yield
    reset_payments()
    reset_idempotency()
    reset_outbox()
