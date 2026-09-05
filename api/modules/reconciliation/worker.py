"""Resolve UNKNOWN payments by asking the provider what actually happened.

UNKNOWN is the state a payment lands in when a provider call times out. It is
deliberately not FAILED: a request that never got an answer may well have
succeeded, and calling it failed is how a customer gets charged twice by a
retry. The cost of that honesty is that something has to come along later and
settle the question. This is that something.

The engine only ever *narrows* uncertainty. UNKNOWN can become CAPTURED or
FAILED because the provider is authoritative about its own ledger; nothing here
moves a payment that has already settled, and a provider answer that disagrees
with a settled local record is recorded as a diff for a human rather than
applied. Automatic correction of a settled payment is how one bad provider
response turns into a wrong refund.

A provider that times out during reconciliation leaves the payment exactly as
it was. Being unable to reach the provider is not evidence about the payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from modules.audit.ledger import AuditLedger
from modules.payments.interface import ProviderError, ProviderTimeout
from modules.payments.provider import get_provider
from modules.payments.state import SETTLED, PaymentState
from repositories.payments import PaymentRepository
from repositories.reconciliation import ReconciliationRepository

logger = structlog.get_logger(__name__)


@dataclass
class ReconciliationReport:
    """What one pass looked at and what it changed."""

    run_id: str
    merchant_id: str
    checked: int = 0
    resolved_captured: int = 0
    resolved_failed: int = 0
    still_unknown: int = 0
    unreachable: int = 0
    diffs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved(self) -> int:
        return self.resolved_captured + self.resolved_failed

    @property
    def clean(self) -> bool:
        """Whether the pass finished with nothing needing a human."""
        return not self.diffs

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "merchant_id": self.merchant_id,
            "checked": self.checked,
            "resolved": self.resolved,
            "resolved_captured": self.resolved_captured,
            "resolved_failed": self.resolved_failed,
            "still_unknown": self.still_unknown,
            "unreachable": self.unreachable,
            "clean": self.clean,
            "diffs": list(self.diffs),
        }


class ReconciliationEngine:
    """Compares local payment state against the provider and closes the gap."""

    def __init__(
        self,
        *,
        provider=None,
        payments: PaymentRepository | None = None,
        runs: ReconciliationRepository | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self._provider = provider or get_provider()
        self._payments = payments or PaymentRepository()
        self._runs = runs or ReconciliationRepository()
        self._ledger = ledger or AuditLedger()

    async def run(
        self, merchant_id: str, *, trigger: str = "scheduled"
    ) -> ReconciliationReport:
        """Reconcile every UNKNOWN payment for one merchant."""
        run = await self._runs.start_run(merchant_id, trigger=trigger)
        report = ReconciliationReport(run_id=run["id"], merchant_id=merchant_id)

        for payment in await self._payments.list_unknown(merchant_id):
            report.checked += 1
            await self._reconcile_one(payment, report)

        await self._runs.finish_run(
            report.run_id,
            checked=report.checked,
            resolved_captured=report.resolved_captured,
            resolved_failed=report.resolved_failed,
            still_unknown=report.still_unknown,
        )
        logger.info(
            "reconciliation_run_complete",
            run_id=report.run_id,
            merchant_id=merchant_id,
            checked=report.checked,
            resolved=report.resolved,
            still_unknown=report.still_unknown,
            diffs=len(report.diffs),
        )
        return report

    async def _reconcile_one(
        self, payment: dict[str, Any], report: ReconciliationReport
    ) -> None:
        """Settle one payment against the provider's answer."""
        payment_id = payment["id"]
        provider_payment_id = payment.get("provider_payment_id")

        if not provider_payment_id:
            # Nothing to ask about. The provider was never reached far enough
            # to hand back an id, so no charge can exist under one.
            report.still_unknown += 1
            await self._diff(
                report,
                payment_id=payment_id,
                kind="no_provider_reference",
                local=payment.get("status"),
                provider=None,
                detail="Payment is UNKNOWN with no provider id to reconcile against",
            )
            return

        try:
            result = await self._provider.get_status(provider_payment_id)
        except ProviderTimeout:
            report.unreachable += 1
            report.still_unknown += 1
            logger.warning("reconciliation_provider_unreachable", payment_id=payment_id)
            return
        except ProviderError as exc:
            report.still_unknown += 1
            await self._diff(
                report,
                payment_id=payment_id,
                kind="provider_error",
                local=payment.get("status"),
                provider=exc.code,
                detail=exc.message or "Provider refused the status query",
            )
            return

        if result.state is PaymentState.CAPTURED:
            await self._resolve_captured(payment, result, report)
            return

        if result.state is PaymentState.FAILED:
            await self._payments.transition(
                payment_id, {PaymentState.UNKNOWN}, PaymentState.FAILED
            )
            report.resolved_failed += 1
            await self._ledger.append(
                merchant_id=payment["merchant_id"],
                entity_type="payment",
                entity_id=payment_id,
                actor="reconciliation",
                action="PAYMENT_RESOLVED_FAILED",
                payload={"run_id": report.run_id, "provider_raw_status": result.raw_status},
                correlation_id=report.run_id,
            )
            return

        if result.state in SETTLED:
            # Refunded or partially refunded while we thought it unknown. That
            # is a real disagreement about money and is not auto-applied.
            report.still_unknown += 1
            await self._diff(
                report,
                payment_id=payment_id,
                kind="unexpected_settled_state",
                local=payment.get("status"),
                provider=result.state.value,
                detail="Provider reports a settled state we never recorded",
            )
            return

        report.still_unknown += 1
        if result.unrecognised:
            await self._diff(
                report,
                payment_id=payment_id,
                kind="unrecognised_provider_status",
                local=payment.get("status"),
                provider=result.raw_status,
                detail="Provider returned a status this system has no mapping for",
            )

    async def _resolve_captured(
        self,
        payment: dict[str, Any],
        result: Any,
        report: ReconciliationReport,
    ) -> None:
        """Mark a payment captured for the amount it was authorised for.

        The amount comes from our own record, never from the provider response.
        The provider is authoritative about *whether* money moved; the order is
        authoritative about *how much* was owed, and taking the amount from the
        event is what a forged or replayed response would exploit.
        """
        payment_id = payment["id"]
        expected = int(payment["amount_paise"])

        await self._payments.mark_captured(
            payment_id,
            amount_paise=expected,
            provider_payment_id=payment.get("provider_payment_id"),
        )
        report.resolved_captured += 1
        await self._ledger.append(
            merchant_id=payment["merchant_id"],
            entity_type="payment",
            entity_id=payment_id,
            actor="reconciliation",
            action="PAYMENT_RESOLVED_CAPTURED",
            payload={
                "run_id": report.run_id,
                "amount_paise": expected,
                "provider_raw_status": result.raw_status,
            },
            correlation_id=report.run_id,
        )
        logger.info("reconciliation_resolved_captured", payment_id=payment_id)

    async def _diff(
        self,
        report: ReconciliationReport,
        *,
        payment_id: str,
        kind: str,
        local: Any,
        provider: Any,
        detail: str,
    ) -> None:
        """Record a disagreement in both the run and the report."""
        entry = {
            "payment_id": payment_id,
            "kind": kind,
            "local": local,
            "provider": provider,
            "detail": detail,
        }
        report.diffs.append(entry)
        await self._runs.record_diff(
            report.run_id,
            payment_id=payment_id,
            kind=kind,
            local=local,
            provider=provider,
            detail=detail,
        )
        # Fields named explicitly rather than splatted. structlog takes the log
        # message as its own ``event`` key, so a dict splat that ever grows an
        # "event" entry raises TypeError from inside the logging call — the
        # error surfaces as a 500 in an unrelated handler, which is a miserable
        # thing to debug.
        logger.warning(
            "reconciliation_diff",
            run_id=report.run_id,
            payment_id=payment_id,
            kind=kind,
            local=local,
            provider=provider,
            detail=detail,
        )

    async def status(self, merchant_id: str) -> dict[str, Any]:
        """The current reconciliation picture for one merchant."""
        latest = await self._runs.latest(merchant_id)
        outstanding = await self._payments.list_unknown(merchant_id)
        return {
            "merchant_id": merchant_id,
            "unknown_payments": len(outstanding),
            "last_run": latest,
            "healthy": bool(latest) and not (latest or {}).get("diffs"),
        }


__all__ = ["ReconciliationEngine", "ReconciliationReport"]
