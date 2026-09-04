"""The payment state machine, and the transitions it refuses."""

import pytest

from modules.payments.state import (
    HOLDS_MONEY,
    TERMINAL,
    TRANSITIONS,
    PaymentState,
    assert_transition,
    can_transition,
    coerce,
    refund_state_for,
)


def test_created_moves_only_where_it_should():
    assert can_transition(PaymentState.CREATED, PaymentState.AUTH_REQUIRED)
    assert can_transition(PaymentState.CREATED, PaymentState.FAILED)
    assert not can_transition(PaymentState.CREATED, PaymentState.CAPTURED)


def test_captured_never_becomes_failed():
    """Money that was taken cannot be un-taken by a status update."""
    assert not can_transition(PaymentState.CAPTURED, PaymentState.FAILED)


def test_every_state_is_enumerated():
    for state in PaymentState:
        assert state in TRANSITIONS


def test_terminal_states_have_no_exit():
    for terminal in TERMINAL:
        assert TRANSITIONS[terminal] == set()


def test_captured_holds_money():
    assert PaymentState.CAPTURED in HOLDS_MONEY


def test_partial_refund_is_partial():
    state = refund_state_for(PaymentState.CAPTURED, refunded_paise=100, captured_paise=1000)
    assert state is PaymentState.PARTIALLY_REFUNDED


def test_full_refund_is_full():
    state = refund_state_for(PaymentState.CAPTURED, refunded_paise=1000, captured_paise=1000)
    assert state is PaymentState.REFUNDED


@pytest.mark.parametrize("raw", ["unknown_state", None, "", "not a status"])
def test_unrecognised_status_becomes_unknown(raw):
    assert coerce(raw) is PaymentState.UNKNOWN


def test_coerce_is_forgiving_about_whitespace_and_case():
    assert coerce("captured") is PaymentState.CAPTURED
    assert coerce("  FAILED  ") is PaymentState.FAILED


def test_invalid_transition_raises():
    with pytest.raises(ValueError):
        assert_transition(PaymentState.CAPTURED, PaymentState.FAILED)


def test_valid_transition_does_not_raise():
    assert_transition(PaymentState.CAPTURED, PaymentState.PARTIALLY_REFUNDED)
