"""The payment path: idempotency, snapshot, provider, persistence.

Two properties this module exists to hold:

**The amount is never taken from the request.** It is read from the order. A
body field that could set the amount is a price-tampering endpoint with extra
steps, so ``PaymentCreateRequest`` has no amount at all and this service ignores
one if a caller sends it anyway.

**A timeout is not a failure.** When the provider does not answer, the payment
goes to UNKNOWN and the idempotency key stays claimed. Calling that "failed"
would let a retry charge a card that was already charged; UNKNOWN is the honest
state and reconciliation resolves it.
"""

from typing import Any

from modules.idempotency.service import IdempotencyService, IdempotencyVerdict
from modules.payments.interface import ProviderError, ProviderTimeout
from modules.payments.provider import get_provider
from modules.payments.snapshot import order_snapshot, snapshot_hash
from modules.payments.state import HOLDS_MONEY, SETTLED, PaymentState
from repositories.orders import OrderRepository
from repositories.outbox import OutboxRepository
from repositories.payments import PaymentRepository

CREATE_ENDPOINT = "/payments"
REFUND_ENDPOINT = "/payments/refund"


def _error(status_code: int, code: str, message: str) -> dict[str, Any]:
    return {"status_code": status_code, "body": {"error": {"code": code, "message": message}}}


class PaymentService:
    """Creates, reads and refunds payments."""

    def __init__(
        self,
        *,
        provider=None,
        payments: PaymentRepository | None = None,
        orders: OrderRepository | None = None,
        idempotency: IdempotencyService | None = None,
        outbox: OutboxRepository | None = None,
    ):
        self._provider = provider or get_provider()
        self._payments = payments or PaymentRepository()
        self._orders = orders or OrderRepository()
        self._idempotency = idempotency or IdempotencyService()
        self._outbox = outbox or OutboxRepository()

    # --- create -------------------------------------------------------------

    async def create_payment(
        self,
        *,
        merchant_id: str,
        order_id: str,
        authorization_id: str,
        idempotency_key: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """Take money for an order, exactly once."""
        verdict = await self._idempotency.claim(
            merchant_id, CREATE_ENDPOINT, idempotency_key, request_body
        )
        if verdict is IdempotencyVerdict.REPLAY:
            stored = await self._idempotency.replay_response(
                merchant_id, CREATE_ENDPOINT, idempotency_key
            )
            if stored:
                status_code, body = stored
                return {"status_code": status_code, "body": body}
            return _error(409, "IDEMPOTENCY_IN_PROGRESS", "This key is still being processed")
        if verdict is IdempotencyVerdict.CONFLICT:
            return _error(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "This idempotency key was used for a different request",
            )
        if verdict is IdempotencyVerdict.IN_PROGRESS:
            return _error(409, "IDEMPOTENCY_IN_PROGRESS", "This key is still being processed")

        order = await self._orders.get(order_id)
        if not order or order.get("merchant_id") != merchant_id:
            await self._idempotency.release(merchant_id, CREATE_ENDPOINT, idempotency_key)
            return _error(404, "ORDER_NOT_FOUND", "No such order")

        try:
            snapshot = order_snapshot(order)
            order_hash = snapshot_hash(order)
        except ValueError as exc:
            await self._idempotency.release(merchant_id, CREATE_ENDPOINT, idempotency_key)
            return _error(422, "ORDER_NOT_PAYABLE", str(exc))

        # The one source of truth for the amount.
        amount_paise = int(order["final_amount_paise"])
        currency = order.get("currency", "INR")

        payment = await self._payments.create(
            merchant_id=merchant_id,
            order_id=order_id,
            amount_paise=amount_paise,
            idempotency_key=idempotency_key,
            order_snapshot=snapshot,
            order_snapshot_hash=order_hash,
        )
        payment_id = payment["id"]

        try:
            created = await self._provider.create_order(
                amount_paise=amount_paise,
                currency=currency,
                reference_id=payment_id,
                idempotency_key=idempotency_key,
            )
            await self._payments.record_attempt(
                payment_id,
                "create_order",
                provider_payment_id=created.provider_payment_id,
                provider_raw_status=created.raw_status,
            )
            await self._payments.transition(
                payment_id, {PaymentState.CREATED}, PaymentState.AUTH_REQUIRED
            )
            await self._payments.transition(
                payment_id, {PaymentState.AUTH_REQUIRED}, PaymentState.AUTHORIZED
            )

            captured = await self._provider.capture(
                provider_payment_id=created.provider_payment_id,
                amount_paise=amount_paise,
                currency=currency,
                idempotency_key=idempotency_key,
            )
            await self._payments.record_attempt(
                payment_id,
                "capture",
                provider_payment_id=captured.provider_payment_id,
                provider_raw_status=captured.raw_status,
            )
        except ProviderTimeout as exc:
            # Unknown, not failed. The key stays claimed on purpose.
            await self._payments.record_attempt(payment_id, "capture", error=str(exc))
            await self._payments.transition(
                payment_id,
                {PaymentState.CREATED, PaymentState.AUTH_REQUIRED, PaymentState.AUTHORIZED},
                PaymentState.UNKNOWN,
            )
            return _error(502, "PROVIDER_UNKNOWN", "Provider did not answer; payment is UNKNOWN")
        except ProviderError as exc:
            await self._payments.record_attempt(payment_id, "capture", error=str(exc))
            await self._payments.transition(
                payment_id,
                {PaymentState.CREATED, PaymentState.AUTH_REQUIRED, PaymentState.AUTHORIZED},
                PaymentState.FAILED,
            )
            await self._idempotency.release(merchant_id, CREATE_ENDPOINT, idempotency_key)
            return _error(402, exc.code, exc.message or "Provider refused the payment")

        if captured.state is not PaymentState.CAPTURED:
            await self._payments.transition(
                payment_id, {PaymentState.AUTHORIZED}, PaymentState.UNKNOWN
            )
            return _error(502, "PROVIDER_UNKNOWN", "Provider returned an unrecognised status")

        record = await self._payments.mark_captured(
            payment_id,
            amount_paise=amount_paise,
            provider_payment_id=captured.provider_payment_id,
        )
        await self._orders.mark_paid(order_id, payment_id=payment_id)
        await self._outbox.emit(
            payment_id,
            "payment.captured",
            {"payment_id": payment_id, "order_id": order_id, "amount_paise": amount_paise},
        )

        body = self._to_out(record)
        await self._idempotency.complete(
            merchant_id, CREATE_ENDPOINT, idempotency_key, 201, body
        )
        return {"status_code": 201, "body": body}

    # --- read ---------------------------------------------------------------

    async def get_payment(self, payment_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Fetch one payment, scoped to its merchant."""
        payment = await self._payments.get(payment_id, merchant_id=merchant_id)
        return self._to_out(payment) if payment else None

    async def get_status(self, payment_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Report status, reconciling an UNKNOWN payment against the provider."""
        payment = await self._payments.get(payment_id, merchant_id=merchant_id)
        if not payment:
            return None

        if PaymentState(payment["status"]) is PaymentState.UNKNOWN:
            payment = await self._reconcile(payment) or payment

        state = PaymentState(payment["status"])
        return {"id": payment["id"], "status": state.value, "settled": state in SETTLED}

    async def _reconcile(self, payment: dict[str, Any]) -> dict[str, Any] | None:
        """Ask the provider what actually happened."""
        provider_payment_id = payment.get("provider_payment_id")
        if not provider_payment_id:
            return None
        try:
            result = await self._provider.get_status(provider_payment_id)
        except (ProviderError, ProviderTimeout):
            return None

        if result.state is PaymentState.CAPTURED:
            return await self._payments.mark_captured(
                payment["id"], amount_paise=int(payment["amount_paise"])
            )
        if result.state is PaymentState.FAILED:
            return await self._payments.transition(
                payment["id"], {PaymentState.UNKNOWN}, PaymentState.FAILED
            )
        return None

    # --- refund -------------------------------------------------------------

    async def refund_payment(
        self,
        *,
        merchant_id: str,
        payment_id: str,
        authorization_id: str,
        amount_paise: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Send money back, never more than was taken."""
        request_body = {
            "payment_id": payment_id,
            "amount_paise": amount_paise,
            "authorization_id": authorization_id,
        }
        verdict = await self._idempotency.claim(
            merchant_id, REFUND_ENDPOINT, idempotency_key, request_body
        )
        if verdict is IdempotencyVerdict.REPLAY:
            stored = await self._idempotency.replay_response(
                merchant_id, REFUND_ENDPOINT, idempotency_key
            )
            if stored:
                status_code, body = stored
                return {"status_code": status_code, "body": body}
            return _error(409, "IDEMPOTENCY_IN_PROGRESS", "This key is still being processed")
        if verdict is not IdempotencyVerdict.CLAIMED:
            return _error(409, "IDEMPOTENCY_CONFLICT", "This idempotency key is not available")

        payment = await self._payments.get(payment_id, merchant_id=merchant_id)
        if not payment:
            await self._idempotency.release(merchant_id, REFUND_ENDPOINT, idempotency_key)
            return _error(404, "PAYMENT_NOT_FOUND", "No such payment")

        if PaymentState(payment["status"]) not in HOLDS_MONEY:
            await self._idempotency.release(merchant_id, REFUND_ENDPOINT, idempotency_key)
            return _error(
                409,
                "PAYMENT_HOLDS_NO_MONEY",
                f"Payment is {payment['status']}; there is nothing to refund",
            )

        remaining = int(payment["captured_paise"]) - int(payment["refunded_paise"])
        if amount_paise > remaining:
            await self._idempotency.release(merchant_id, REFUND_ENDPOINT, idempotency_key)
            return _error(
                422,
                "REFUND_EXCEEDS_CAPTURE",
                f"Refund of {amount_paise} exceeds the {remaining} still refundable",
            )

        try:
            await self._provider.refund(
                provider_payment_id=payment.get("provider_payment_id") or payment_id,
                amount_paise=amount_paise,
                idempotency_key=idempotency_key,
            )
        except ProviderTimeout as exc:
            await self._payments.record_attempt(payment_id, "refund", error=str(exc))
            return _error(502, "PROVIDER_UNKNOWN", "Provider did not answer; refund is UNKNOWN")
        except ProviderError as exc:
            await self._payments.record_attempt(payment_id, "refund", error=str(exc))
            await self._idempotency.release(merchant_id, REFUND_ENDPOINT, idempotency_key)
            return _error(402, exc.code, exc.message or "Provider refused the refund")

        record = await self._payments.record_refund(payment_id, amount_paise=amount_paise)
        # The order repository is Phase 4's; it may not track refunds yet, and a
        # payment refund must not depend on that having landed.
        order_refund = getattr(self._orders, "record_refund", None)
        if order_refund is not None:
            await order_refund(payment["order_id"], amount_paise=amount_paise)
        await self._outbox.emit(
            payment_id,
            "payment.refunded",
            {"payment_id": payment_id, "amount_paise": amount_paise},
        )

        body = self._to_out(record)
        await self._idempotency.complete(
            merchant_id, REFUND_ENDPOINT, idempotency_key, 200, body
        )
        return {"status_code": 200, "body": body}

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _to_out(payment: dict[str, Any]) -> dict[str, Any]:
        """Project the stored record onto the response contract."""
        return {
            "id": payment["id"],
            "status": payment["status"],
            "amount_paise": payment["amount_paise"],
            "captured_paise": payment["captured_paise"],
            "refunded_paise": payment["refunded_paise"],
            "order_snapshot": payment.get("order_snapshot"),
            "order_snapshot_hash": payment.get("order_snapshot_hash"),
        }


__all__ = ["PaymentService"]
