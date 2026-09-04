"""Mock Razorpay for local dev and tests.

Deterministic on the idempotency key, so the same key produces the same payment
id however many times it is called — which is what makes a retry test meaningful
rather than merely green. Failures are injected, never random: a test that fails
intermittently teaches nobody anything.
"""

import hashlib
from dataclasses import dataclass, field
from uuid import uuid4

from modules.payments.interface import ProviderError, ProviderResult, ProviderTimeout
from modules.payments.state import PaymentState


class RazorpayMockService:
    """Payment-link mock used by the checkout session flow."""

    async def create_payment_link(
        self,
        *,
        amount_paise: int,
        description: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        link_id = f"plink_mock_{uuid4().hex[:12]}"
        return {
            "payment_link_id": link_id,
            "payment_link_url": f"https://rzp.io/mock/{link_id}",
            "expires_at": None,
        }

    async def simulate_payment(self, payment_link_id: str) -> dict[str, str]:
        return {"payment_id": f"pay_mock_{uuid4().hex[:12]}", "payment_link_id": payment_link_id}


_INJECTED = ("success", "error", "timeout")


@dataclass
class Behaviour:
    """Injection points for deterministic testing."""

    create_order: str = "success"
    capture: str = "success"
    refund: str = "success"
    status_sequence: list[str] = field(default_factory=list)


class RazorpayMockProvider:
    """Mock provider with deterministic ids and injectable behaviour."""

    def __init__(self, behaviour: Behaviour | None = None):
        self._behaviour = behaviour or Behaviour()
        self._results: dict[str, ProviderResult] = {}
        self._calls: dict[str, int] = {}
        self._captured: dict[str, int] = {}
        self._refunded: dict[str, int] = {}
        self._status_calls: dict[str, int] = {}

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        reference_id: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Create an order; the id is a pure function of the idempotency key."""
        self._raise_for(self._behaviour.create_order, "GATEWAY_ERROR", "Mock create error")

        # Not a security decision: this is a short stable id for a fake gateway.
        key_hash = hashlib.md5(idempotency_key.encode(), usedforsecurity=False).hexdigest()[:12]
        provider_payment_id = f"pay_mock_{key_hash}"

        result = ProviderResult(
            provider_payment_id=provider_payment_id,
            provider_order_id=f"order_mock_{key_hash}",
            state=PaymentState.CREATED,
            raw_status="created",
        )
        self._results[provider_payment_id] = result
        self._captured.setdefault(provider_payment_id, 0)
        self._refunded.setdefault(provider_payment_id, 0)
        self._track("create_order")
        return result

    async def capture(
        self,
        provider_payment_id: str,
        amount_paise: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Capture, or return whatever odd status the behaviour asked for."""
        self._raise_for(self._behaviour.capture, "CAPTURE_FAILED", "Mock capture error")

        self._captured[provider_payment_id] = amount_paise
        injected = self._behaviour.capture
        raw = "captured" if injected in _INJECTED else injected
        state, unrecognised = (
            (PaymentState.CAPTURED, False) if raw == "captured" else (PaymentState.UNKNOWN, True)
        )

        result = ProviderResult(
            provider_payment_id=provider_payment_id,
            provider_order_id=f"order_{provider_payment_id[:8]}",
            state=state,
            raw_status=raw,
            unrecognised=unrecognised,
        )
        self._results[provider_payment_id] = result
        self._track("capture")
        return result

    async def refund(
        self,
        provider_payment_id: str,
        amount_paise: int,
        idempotency_key: str,
    ) -> ProviderResult:
        """Refund, tracking the running total."""
        self._raise_for(self._behaviour.refund, "REFUND_FAILED", "Mock refund error")

        refunded = self._refunded.get(provider_payment_id, 0) + amount_paise
        self._refunded[provider_payment_id] = refunded
        captured = self._captured.get(provider_payment_id, 0)
        state = (
            PaymentState.REFUNDED if refunded >= captured else PaymentState.PARTIALLY_REFUNDED
        )

        self._track("refund")
        return ProviderResult(
            provider_payment_id=provider_payment_id,
            provider_order_id=f"order_{provider_payment_id[:8]}",
            state=state,
            raw_status=state.value,
        )

    async def get_status(self, provider_payment_id: str) -> ProviderResult:
        """Report status, optionally walking a scripted sequence."""
        from modules.payments.provider import canonical_state

        call = self._status_calls.get(provider_payment_id, 0)
        self._status_calls[provider_payment_id] = call + 1

        sequence = self._behaviour.status_sequence
        if sequence and call < len(sequence):
            raw = sequence[call]
        else:
            known = self._results.get(provider_payment_id)
            raw = known.raw_status if known else "captured"

        state, unrecognised = canonical_state(raw)
        self._track("get_status")
        return ProviderResult(
            provider_payment_id=provider_payment_id,
            provider_order_id=f"order_{provider_payment_id[:8]}",
            state=state,
            raw_status=raw,
            unrecognised=unrecognised,
        )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _raise_for(setting: str, code: str, message: str) -> None:
        if setting == "timeout":
            raise ProviderTimeout()
        if setting == "error":
            raise ProviderError(code=code, message=message)

    def _track(self, operation: str) -> None:
        self._calls[operation] = self._calls.get(operation, 0) + 1

    def call_count(self, operation: str) -> int:
        """How many times an operation was called."""
        return self._calls.get(operation, 0)


__all__ = ["Behaviour", "RazorpayMockProvider", "RazorpayMockService"]
