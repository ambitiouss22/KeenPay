"""The provider boundary: what the gateway says vs what we believe."""

import pytest

from modules.payments.interface import ProviderError, ProviderTimeout
from modules.payments.state import PaymentState
from services.razorpay_mock import Behaviour, RazorpayMockProvider

KEY = "key-abcdefghijklmnop"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("created", PaymentState.CREATED),
        ("attempted", PaymentState.AUTH_REQUIRED),
        ("authorized", PaymentState.AUTHORIZED),
        ("authorised", PaymentState.AUTHORIZED),
        ("captured", PaymentState.CAPTURED),
        ("failed", PaymentState.FAILED),
    ],
)
def test_known_statuses_map_exactly(raw, expected):
    from modules.payments.provider import canonical_state

    state, unrecognised = canonical_state(raw)
    assert state is expected
    assert not unrecognised


def test_an_unknown_status_is_unknown_not_captured():
    """A status we do not recognise must never become a convenient default."""
    from modules.payments.provider import canonical_state

    state, unrecognised = canonical_state("quantum_settled")
    assert state is PaymentState.UNKNOWN
    assert unrecognised


def test_a_missing_status_is_unknown():
    from modules.payments.provider import canonical_state

    assert canonical_state(None)[0] is PaymentState.UNKNOWN
    assert canonical_state("")[0] is PaymentState.UNKNOWN


def test_the_mock_is_importable_without_a_cycle():
    """provider.py and services.razorpay import each other; this must still load."""
    from modules.payments.provider import get_provider

    assert get_provider() is not None


async def test_the_same_key_produces_the_same_payment():
    provider = RazorpayMockProvider()
    first = await provider.create_order(100, "INR", "ord_1", KEY)
    second = await provider.create_order(100, "INR", "ord_1", KEY)
    assert first.provider_payment_id == second.provider_payment_id


async def test_a_different_key_produces_a_different_payment():
    provider = RazorpayMockProvider()
    first = await provider.create_order(100, "INR", "ord_1", KEY)
    second = await provider.create_order(100, "INR", "ord_1", KEY + "z")
    assert first.provider_payment_id != second.provider_payment_id


async def test_a_timeout_is_not_a_failure():
    provider = RazorpayMockProvider(Behaviour(capture="timeout"))
    with pytest.raises(ProviderTimeout):
        await provider.capture("pay_1", 100, "INR", KEY)


async def test_a_refusal_is_a_provider_error():
    provider = RazorpayMockProvider(Behaviour(capture="error"))
    with pytest.raises(ProviderError):
        await provider.capture("pay_1", 100, "INR", KEY)


async def test_an_unrecognised_capture_status_flows_through_as_unknown():
    provider = RazorpayMockProvider(Behaviour(capture="teleported"))
    result = await provider.capture("pay_1", 100, "INR", KEY)
    assert result.state is PaymentState.UNKNOWN
    assert result.unrecognised


async def test_status_can_walk_out_of_unknown():
    provider = RazorpayMockProvider(Behaviour(status_sequence=["quantum", "captured"]))
    first = await provider.get_status("pay_1")
    second = await provider.get_status("pay_1")
    assert first.state is PaymentState.UNKNOWN
    assert second.state is PaymentState.CAPTURED
