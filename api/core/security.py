"""JWT validation and payment gate assertions.

assert_payment_gates() must run before any Razorpay payment_links call.
See docs/GUARDRAILS_AND_SAFETY.md.
"""

from typing import Any


def assert_payment_gates(state: dict[str, Any]) -> None:
    """Verify all payment preconditions. Raises PaymentGateError on failure."""
    gates = [
        state.get("guardrail_decision") == "APPROVED",
        state.get("guardrail_decision_id") is not None,
        state.get("user_confirmed_payment") is True,
        state.get("final_amount_paise")
        == (state.get("approved_offer") or {}).get("final_amount_paise"),
        state.get("inventory_reserved") is True,
        state.get("security_block") is False,
    ]
    if not all(gates):
        from core.exceptions import PaymentGateError

        raise PaymentGateError(
            code="payment_gates_failed",
            message="One or more payment gates failed",
            details={"gates_passed": gates},
        )
