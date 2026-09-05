"""Reconciliation must narrow uncertainty and never invent it.

UNKNOWN exists because a timed-out provider call proves nothing. The job of
these tests is to hold that line from both sides: a provider that says
"captured" resolves the payment, and a provider that says nothing useful — or
that cannot be reached at all — leaves it exactly where it was. Being unable to
reach the provider is not evidence about the payment.
"""

from __future__ import annotations

import pytest

from modules.audit.ledger import AuditLedger
from modules.payments.interface import ProviderError, ProviderResult, ProviderTimeout
from modules.payments.state import PaymentState
from modules.reconciliation.worker import ReconciliationEngine
from repositories.payments import PaymentRepository
from repositories.reconciliation import ReconciliationRepository

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"


class ScriptedProvider:
    """A provider that answers exactly what a test tells it to."""

    def __init__(self, answer):
        self._answer = answer
        self.calls = 0

    async def get_status(self, provider_payment_id: str) -> ProviderResult:
        self.calls += 1
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def result(state: PaymentState, raw: str = "", unrecognised: bool = False) -> ProviderResult:
    return ProviderResult(
        provider_payment_id="pay_provider_1",
        provider_order_id="order_provider_1",
        state=state,
        raw_status=raw or state.value,
        unrecognised=unrecognised,
    )


@pytest.fixture
def payments() -> PaymentRepository:
    return PaymentRepository()


async def make_unknown_payment(
    payments: PaymentRepository, *, provider_payment_id: str | None = "pay_provider_1"
) -> dict:
    """A payment stuck in UNKNOWN, the state reconciliation exists to resolve."""
    payment = await payments.create(
        merchant_id=MERCHANT,
        order_id="ord_1",
        amount_paise=449800,
        idempotency_key="idem_reconcile_1",
        order_snapshot={"final_amount_paise": 449800},
        order_snapshot_hash="a" * 64,
    )
    await payments.transition(
        payment["id"], {PaymentState.CREATED}, PaymentState.AUTH_REQUIRED
    )
    await payments.transition(
        payment["id"], {PaymentState.AUTH_REQUIRED}, PaymentState.UNKNOWN
    )
    if provider_payment_id:
        await payments.set_provider_reference(payment["id"], provider_payment_id)
    return await payments.get(payment["id"], merchant_id=MERCHANT)


def engine_for(provider, payments: PaymentRepository) -> ReconciliationEngine:
    return ReconciliationEngine(
        provider=provider,
        payments=payments,
        runs=ReconciliationRepository(),
        ledger=AuditLedger(),
    )


# --- resolving --------------------------------------------------------------


async def test_a_captured_answer_resolves_the_payment(payments):
    payment = await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    report = await engine.run(MERCHANT)

    assert report.checked == 1
    assert report.resolved_captured == 1
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.CAPTURED.value


async def test_the_captured_amount_comes_from_our_record_not_the_provider(payments):
    """The provider is authoritative about whether money moved, not how much."""
    payment = await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    await engine.run(MERCHANT)

    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["captured_paise"] == 449800


async def test_a_failed_answer_resolves_the_payment(payments):
    payment = await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.FAILED)), payments)

    report = await engine.run(MERCHANT)

    assert report.resolved_failed == 1
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.FAILED.value


async def test_resolution_is_written_to_the_ledger(payments):
    await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    await engine.run(MERCHANT)

    entries, total = await AuditLedger().entries_for(
        MERCHANT, action="PAYMENT_RESOLVED_CAPTURED", limit=10
    )
    assert total == 1
    assert entries[0].actor == "reconciliation"


# --- refusing to guess ------------------------------------------------------


async def test_an_unreachable_provider_leaves_the_payment_unknown(payments):
    """Not being able to ask is not an answer."""
    payment = await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(ProviderTimeout()), payments)

    report = await engine.run(MERCHANT)

    assert report.unreachable == 1
    assert report.still_unknown == 1
    assert report.resolved == 0
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.UNKNOWN.value


async def test_a_provider_error_is_recorded_as_a_diff(payments):
    payment = await make_unknown_payment(payments)
    engine = engine_for(
        ScriptedProvider(ProviderError(code="BAD_ID", message="no such payment")), payments
    )

    report = await engine.run(MERCHANT)

    assert report.still_unknown == 1
    assert not report.clean
    assert report.diffs[0]["kind"] == "provider_error"
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.UNKNOWN.value


async def test_an_unrecognised_status_is_a_diff_not_a_resolution(payments):
    """A status we have no mapping for must not be quietly read as success."""
    await make_unknown_payment(payments)
    engine = engine_for(
        ScriptedProvider(result(PaymentState.UNKNOWN, raw="in_limbo", unrecognised=True)),
        payments,
    )

    report = await engine.run(MERCHANT)

    assert report.resolved == 0
    assert report.diffs[0]["kind"] == "unrecognised_provider_status"


async def test_an_unexpected_settled_state_is_not_auto_applied(payments):
    """A refund we never recorded is a disagreement about money, not a fixup."""
    payment = await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.REFUNDED)), payments)

    report = await engine.run(MERCHANT)

    assert report.resolved == 0
    assert report.diffs[0]["kind"] == "unexpected_settled_state"
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.UNKNOWN.value


async def test_a_payment_with_no_provider_id_is_a_diff(payments):
    """Nothing to ask about: the provider was never reached far enough to answer."""
    await make_unknown_payment(payments, provider_payment_id=None)
    provider = ScriptedProvider(result(PaymentState.CAPTURED))
    engine = engine_for(provider, payments)

    report = await engine.run(MERCHANT)

    assert provider.calls == 0
    assert report.diffs[0]["kind"] == "no_provider_reference"


# --- scope and reporting ----------------------------------------------------


async def test_settled_payments_are_never_touched(payments):
    """Reconciliation only narrows uncertainty; it does not revisit settled money."""
    payment = await payments.create(
        merchant_id=MERCHANT,
        order_id="ord_settled",
        amount_paise=1000,
        idempotency_key="idem_settled",
        order_snapshot={},
        order_snapshot_hash="b" * 64,
    )
    await payments.mark_captured(payment["id"], amount_paise=1000)
    provider = ScriptedProvider(result(PaymentState.FAILED))
    engine = engine_for(provider, payments)

    report = await engine.run(MERCHANT)

    assert report.checked == 0
    assert provider.calls == 0
    stored = await payments.get(payment["id"], merchant_id=MERCHANT)
    assert stored["status"] == PaymentState.CAPTURED.value


async def test_another_merchants_payments_are_out_of_scope(payments):
    await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    report = await engine.run("merchant_rival")

    assert report.checked == 0


async def test_a_clean_run_is_reported_as_clean(payments):
    await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    report = await engine.run(MERCHANT)

    assert report.clean
    assert report.diffs == []


async def test_the_run_is_recorded(payments):
    await make_unknown_payment(payments)
    runs = ReconciliationRepository()
    engine = ReconciliationEngine(
        provider=ScriptedProvider(result(PaymentState.CAPTURED)),
        payments=payments,
        runs=runs,
        ledger=AuditLedger(),
    )

    report = await engine.run(MERCHANT, trigger="manual")

    stored = await runs.get(report.run_id, merchant_id=MERCHANT)
    assert stored["status"] == "completed"
    assert stored["trigger"] == "manual"
    assert stored["resolved_captured"] == 1
    assert stored["finished_at"] is not None


async def test_a_runs_diffs_are_recorded_against_it(payments):
    await make_unknown_payment(payments, provider_payment_id=None)
    runs = ReconciliationRepository()
    engine = ReconciliationEngine(
        provider=ScriptedProvider(result(PaymentState.CAPTURED)),
        payments=payments,
        runs=runs,
        ledger=AuditLedger(),
    )

    report = await engine.run(MERCHANT)
    stored = await runs.get(report.run_id, merchant_id=MERCHANT)

    assert len(stored["diffs"]) == 1


async def test_status_reports_outstanding_work(payments):
    await make_unknown_payment(payments)
    engine = engine_for(ScriptedProvider(result(PaymentState.CAPTURED)), payments)

    before = await engine.status(MERCHANT)
    assert before["unknown_payments"] == 1
    assert before["last_run"] is None

    await engine.run(MERCHANT)

    after = await engine.status(MERCHANT)
    assert after["unknown_payments"] == 0
    assert after["last_run"] is not None
    assert after["healthy"] is True
