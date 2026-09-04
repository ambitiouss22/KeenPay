"""Payment persistence and atomic state transitions."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.payments.state import PaymentState, refund_state_for

_MEMORY_PAYMENTS: dict[str, dict[str, Any]] = {}


def reset_payments() -> None:
    """Drop every payment. For test isolation only."""
    _MEMORY_PAYMENTS.clear()


class PaymentRepository:
    """Payment records for one merchant."""

    async def create(
        self,
        *,
        merchant_id: str,
        order_id: str,
        amount_paise: int,
        idempotency_key: str,
        order_snapshot: dict[str, Any],
        order_snapshot_hash: str,
    ) -> dict[str, Any]:
        """Open a payment in CREATED."""
        payment_id = f"pay_{uuid4().hex[:12]}"
        now = datetime.now(UTC)
        record = {
            "id": payment_id,
            "merchant_id": merchant_id,
            "order_id": order_id,
            "amount_paise": amount_paise,
            "captured_paise": 0,
            "refunded_paise": 0,
            "status": PaymentState.CREATED.value,
            "order_snapshot": order_snapshot,
            "order_snapshot_hash": order_snapshot_hash,
            "idempotency_key": idempotency_key,
            "provider_payment_id": None,
            "attempts": [],
            "created_at": now,
            "updated_at": now,
        }
        _MEMORY_PAYMENTS[payment_id] = record
        return dict(record)

    async def get(self, payment_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Fetch one payment, scoped to its merchant.

        Another merchant's payment is reported absent, never forbidden: a 403 on
        a record that exists confirms the id is real to whoever is guessing.
        """
        payment = _MEMORY_PAYMENTS.get(payment_id)
        if payment and payment.get("merchant_id") == merchant_id:
            return dict(payment)
        return None

    async def transition(
        self,
        payment_id: str,
        expected: set[PaymentState],
        target: PaymentState,
    ) -> dict[str, Any] | None:
        """Move to ``target`` only from one of ``expected``.

        ``None`` when the record was not in an expected state. The read and the
        write happen together with no await between them, so a concurrent caller
        finds nothing to transition rather than transitioning twice.
        """
        payment = _MEMORY_PAYMENTS.get(payment_id)
        if not payment:
            return None
        if PaymentState(payment["status"]) not in expected:
            return None

        payment["status"] = target.value
        payment["updated_at"] = datetime.now(UTC)
        return dict(payment)

    async def mark_captured(
        self,
        payment_id: str,
        *,
        amount_paise: int,
        provider_payment_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Record the captured amount."""
        payment = _MEMORY_PAYMENTS.get(payment_id)
        if not payment:
            return None
        payment["captured_paise"] = amount_paise
        payment["status"] = PaymentState.CAPTURED.value
        if provider_payment_id:
            payment["provider_payment_id"] = provider_payment_id
        payment["updated_at"] = datetime.now(UTC)
        return dict(payment)

    async def record_refund(self, payment_id: str, *, amount_paise: int) -> dict[str, Any] | None:
        """Add to the refunded total and set the resulting state."""
        payment = _MEMORY_PAYMENTS.get(payment_id)
        if not payment:
            return None
        refunded = int(payment.get("refunded_paise") or 0) + amount_paise
        captured = int(payment.get("captured_paise") or 0)
        payment["refunded_paise"] = refunded
        payment["status"] = refund_state_for(
            PaymentState(payment["status"]), refunded, captured
        ).value
        payment["updated_at"] = datetime.now(UTC)
        return dict(payment)

    async def record_attempt(
        self,
        payment_id: str,
        operation: str,
        *,
        provider_payment_id: str | None = None,
        provider_raw_status: str | None = None,
        error: str | None = None,
    ) -> None:
        """Append one provider call to the payment's history."""
        payment = _MEMORY_PAYMENTS.get(payment_id)
        if not payment:
            return
        payment["attempts"].append(
            {
                "operation": operation,
                "provider_payment_id": provider_payment_id,
                "provider_raw_status": provider_raw_status,
                "error": error,
                "at": datetime.now(UTC),
            }
        )

    async def list_unknown(self, merchant_id: str) -> list[dict[str, Any]]:
        """Payments stuck in UNKNOWN, for reconciliation."""
        return [
            dict(payment)
            for payment in _MEMORY_PAYMENTS.values()
            if payment.get("merchant_id") == merchant_id
            and payment.get("status") == PaymentState.UNKNOWN.value
        ]


__all__ = ["PaymentRepository", "reset_payments"]
