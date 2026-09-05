"""Security-test fixtures.

The webhook store is a dedupe by design: an event id it has already seen is
refused forever. Without a reset between tests, the second test to reuse a
fixture id gets "duplicate" instead of the behaviour it is asserting, and the
failure points at the handler rather than at the shared state.
"""

import pytest

from modules.audit.ledger import reset_ledger
from repositories.idempotency import reset_idempotency
from repositories.orders import reset_orders
from repositories.outbox import reset_outbox
from repositories.payments import reset_payments
from repositories.reconciliation import reset_reconciliation
from repositories.webhooks import reset_webhooks


@pytest.fixture(autouse=True)
def _reset_stores():
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
