"""Payment state machine with explicit transitions."""

from enum import Enum


class PaymentState(Enum):
    """Canonical payment states."""

    CREATED = "created"
    AUTH_REQUIRED = "auth_required"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    UNKNOWN = "unknown"
    FAILED = "failed"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


# Explicit state machine: every valid transition enumerated.
TRANSITIONS = {
    PaymentState.CREATED: {PaymentState.AUTH_REQUIRED, PaymentState.UNKNOWN, PaymentState.FAILED},
    PaymentState.AUTH_REQUIRED: {
        PaymentState.AUTHORIZED,
        PaymentState.UNKNOWN,
        PaymentState.FAILED,
    },
    PaymentState.AUTHORIZED: {PaymentState.CAPTURED, PaymentState.UNKNOWN, PaymentState.FAILED},
    PaymentState.CAPTURED: {PaymentState.PARTIALLY_REFUNDED, PaymentState.REFUNDED},
    PaymentState.UNKNOWN: {PaymentState.CAPTURED, PaymentState.FAILED},
    PaymentState.FAILED: set(),
    PaymentState.PARTIALLY_REFUNDED: {PaymentState.REFUNDED},
    PaymentState.REFUNDED: set(),
}

TERMINAL = {PaymentState.FAILED, PaymentState.REFUNDED}
HOLDS_MONEY = {PaymentState.CAPTURED, PaymentState.PARTIALLY_REFUNDED}
SETTLED = {PaymentState.CAPTURED, PaymentState.PARTIALLY_REFUNDED, PaymentState.REFUNDED}


def coerce(raw: str | None) -> PaymentState:
    """Convert a string to a state, defaulting to UNKNOWN rather than guessing."""
    if not raw:
        return PaymentState.UNKNOWN
    try:
        return PaymentState((raw or "").strip().lower())
    except ValueError:
        return PaymentState.UNKNOWN


def can_transition(from_state: PaymentState, to_state: PaymentState) -> bool:
    """Check whether a transition is allowed."""
    return to_state in TRANSITIONS.get(from_state, set())


def assert_transition(from_state: PaymentState, to_state: PaymentState) -> None:
    """Raise if a transition is not allowed."""
    if not can_transition(from_state, to_state):
        raise ValueError(f"Cannot transition from {from_state.value} to {to_state.value}")


def refund_state_for(
    current: PaymentState,
    refunded_paise: int,
    captured_paise: int,
) -> PaymentState:
    """Decide the state a refund leaves the payment in, from the amounts alone."""
    if refunded_paise >= captured_paise:
        return PaymentState.REFUNDED
    return PaymentState.PARTIALLY_REFUNDED
