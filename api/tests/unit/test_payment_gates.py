"""Unit tests for payment gate assertions."""

import pytest

from core.exceptions import PaymentGateError
from core.security import assert_payment_gates


def _valid_state() -> dict:
    return {
        "guardrail_decision": "APPROVED",
        "guardrail_decision_id": "dec_123",
        "user_confirmed_payment": True,
        "final_amount_paise": 449800,
        "approved_offer": {"final_amount_paise": 449800},
        "inventory_reserved": True,
        "security_block": False,
    }


def test_all_gates_pass():
    assert_payment_gates(_valid_state())


@pytest.mark.parametrize(
    "override",
    [
        {"guardrail_decision": "REJECTED"},
        {"guardrail_decision_id": None},
        {"user_confirmed_payment": False},
        {"final_amount_paise": 1},
        {"inventory_reserved": False},
        {"security_block": True},
    ],
)
def test_gate_failure(override):
    state = _valid_state()
    state.update(override)
    with pytest.raises(PaymentGateError):
        assert_payment_gates(state)
