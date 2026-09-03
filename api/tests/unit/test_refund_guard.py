"""Refund eligibility.

Every case here is a subtraction someone has got wrong in production: refunding
more than was captured, forgetting to deduct earlier refunds, refunding an
order that was never paid, refunding after the acquirer's window closed so the
money leaves the merchant without reaching the cardholder.

A refund fails quietly in a way a payment does not. A declined card is
immediate and visible; an over-refund succeeds, the customer is pleased, and it
surfaces a month later during reconciliation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from config.policy import MerchantPolicy
from core.exceptions import ValidationError
from modules.refunds.guard import REFUNDABLE_STATUSES, RefundGuard, refundable_paise

POLICY = MerchantPolicy()
GUARD = RefundGuard(policy=POLICY)
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def order(**overrides) -> dict:
    base = {
        "id": "ord_abc123",
        "merchant_id": "merchant_keen",
        "status": "paid",
        "final_amount_paise": 100_000,
        "refunded_paise": 0,
        "paid_at": NOW - timedelta(days=3),
    }
    return {**base, **overrides}


def verdict(*, amount=50_000, ord_overrides=None, now=NOW, merchant="merchant_keen"):
    return GUARD.evaluate(
        order=order(**(ord_overrides or {})),
        merchant_id=merchant,
        amount_paise=amount,
        now=now,
    )


# --- the happy path ---------------------------------------------------------


def test_a_partial_refund_on_a_recent_paid_order_is_eligible():
    result = verdict()
    assert result.eligible, result.reasons
    assert result.reasons == []
    assert result.max_refundable_paise == 100_000


def test_a_full_refund_is_eligible():
    assert verdict(amount=100_000).eligible


def test_the_verdict_carries_the_numbers_it_reasoned_from():
    details = verdict().details
    assert details["captured_paise"] == 100_000
    assert details["already_refunded_paise"] == 0
    assert details["refund_window_days"] == POLICY.refund_window_days


# --- arithmetic -------------------------------------------------------------


def test_refunding_more_than_was_captured_is_refused():
    result = verdict(amount=100_001)
    assert not result.eligible
    assert any("still refundable" in r for r in result.reasons)


def test_earlier_refunds_are_deducted():
    """Without the subtraction the same money leaves twice."""
    result = verdict(amount=60_000, ord_overrides={"refunded_paise": 50_000})
    assert not result.eligible
    assert result.max_refundable_paise == 50_000


def test_a_second_partial_refund_within_the_remainder_is_fine():
    assert verdict(amount=50_000, ord_overrides={"refunded_paise": 50_000}).eligible


def test_a_fully_refunded_order_has_nothing_left():
    result = verdict(amount=1, ord_overrides={"refunded_paise": 100_000})
    assert not result.eligible
    assert result.max_refundable_paise == 0
    assert any("fully refunded" in r for r in result.reasons)


def test_refundable_is_floored_at_zero():
    """A negative remainder would compare as less than any request and report
    the wrong failure, hiding the real problem: the books already disagree."""
    assert refundable_paise({"final_amount_paise": 100, "refunded_paise": 500}) == 0


def test_a_zero_refund_is_refused():
    assert not verdict(amount=0).eligible


def test_a_negative_refund_is_refused():
    """Otherwise a "refund" of minus ten thousand is a charge nobody approved."""
    assert not verdict(amount=-10_000).eligible


def test_a_float_amount_is_rejected_outright():
    """Not merely refused - raised. A float here silently refunds 249 paise
    instead of 249.9, and unlike a mispriced sale nobody complains."""
    with pytest.raises(ValidationError):
        verdict(amount=499.5)


def test_a_boolean_amount_is_rejected():
    """bool subclasses int; True would otherwise refund one paisa."""
    with pytest.raises(ValidationError):
        verdict(amount=True)


# --- order state ------------------------------------------------------------


def test_an_unpaid_order_cannot_be_refunded():
    result = verdict(ord_overrides={"status": "pending", "paid_at": None})
    assert not result.eligible
    assert any("not refundable" in r for r in result.reasons)


@pytest.mark.parametrize("status", ["pending", "failed", "cancelled", "expired", ""])
def test_only_captured_money_comes_back(status):
    assert not verdict(ord_overrides={"status": status}).eligible


@pytest.mark.parametrize("status", sorted(REFUNDABLE_STATUSES))
def test_every_refundable_status_is_accepted(status):
    assert verdict(ord_overrides={"status": status}).eligible


def test_an_unknown_status_is_refused_by_default():
    """An allow-list. A status nobody anticipated must not be refundable."""
    assert not verdict(ord_overrides={"status": "settled_maybe"}).eligible


def test_a_missing_order_is_not_eligible():
    result = GUARD.evaluate(order=None, merchant_id="merchant_keen", amount_paise=100)
    assert not result.eligible
    assert result.reasons == ["order not found"]


def test_another_merchants_order_is_reported_as_missing():
    """Same answer as a missing order, and no detail about the real owner:
    confirming an id belongs to *someone* maps another merchant's orders."""
    result = verdict(merchant="merchant_acme")
    assert not result.eligible
    assert result.reasons == ["order not found"]


# --- the refund window ------------------------------------------------------


def test_a_refund_inside_the_window_is_eligible():
    old = NOW - timedelta(days=POLICY.refund_window_days - 1)
    assert verdict(ord_overrides={"paid_at": old}).eligible


def test_a_refund_after_the_window_is_refused():
    stale = NOW - timedelta(days=POLICY.refund_window_days + 1)
    result = verdict(ord_overrides={"paid_at": stale})
    assert not result.eligible
    assert any("refund window" in r for r in result.reasons)


def test_the_window_boundary_is_inclusive():
    exactly = NOW - timedelta(days=POLICY.refund_window_days)
    assert verdict(ord_overrides={"paid_at": exactly}).eligible


def test_a_paid_order_with_no_capture_timestamp_fails_closed():
    """An order that cannot be aged cannot be shown to be inside the window."""
    result = verdict(ord_overrides={"paid_at": None})
    assert not result.eligible
    assert any("capture timestamp" in r for r in result.reasons)


def test_a_naive_timestamp_is_read_as_utc():
    naive = (NOW - timedelta(days=2)).replace(tzinfo=None)
    assert verdict(ord_overrides={"paid_at": naive}).eligible


def test_the_verdict_is_reproducible_for_a_past_moment():
    """"Was this eligible at the time?" gets asked during chargeback disputes,
    long after the clock has moved on."""
    stale = NOW - timedelta(days=POLICY.refund_window_days + 10)
    then = GUARD.evaluate(
        order=order(paid_at=stale),
        merchant_id="merchant_keen",
        amount_paise=1_000,
        now=stale + timedelta(days=1),
    )
    now = verdict(amount=1_000, ord_overrides={"paid_at": stale})
    assert then.eligible
    assert not now.eligible


# --- reporting --------------------------------------------------------------


def test_every_reason_is_collected_not_just_the_first():
    """A refund refused for four reasons that reports one sends an operator
    round the loop four times."""
    result = verdict(
        amount=999_999,
        ord_overrides={"status": "cancelled", "paid_at": NOW - timedelta(days=400)},
    )
    assert len(result.reasons) >= 3


def test_a_large_refund_is_flagged_as_needing_authorization():
    big = POLICY.escalate_refund_above_paise + 1
    result = verdict(
        amount=big, ord_overrides={"final_amount_paise": big + 1_000}
    )
    assert result.eligible
    assert result.requires_authorization


def test_a_small_refund_is_not_flagged():
    assert not verdict(amount=1_000).requires_authorization


def test_an_ineligible_refund_is_never_flagged_as_authorizable():
    """Otherwise a caller could read requires_authorization as a green light."""
    assert not verdict(amount=999_999).requires_authorization


def test_a_verdict_serialises_for_the_wire():
    payload = verdict().to_dict()
    assert set(payload) == {
        "eligible",
        "max_refundable_paise",
        "reasons",
        "requires_authorization",
        "details",
    }


# --- handing off to the gate ------------------------------------------------


def test_the_action_reads_its_money_from_the_order_not_the_caller():
    """A caller able to name what was captured could name a larger number and
    refund against it."""
    action = RefundGuard.to_action(
        order=order(final_amount_paise=100_000, refunded_paise=25_000),
        merchant_id="merchant_keen",
        amount_paise=10_000,
        actor_id="mgr_1",
        actor_role="manager",
    )
    assert action.captured_paise == 100_000
    assert action.already_refunded_paise == 25_000
    assert action.subject_id == "ord_abc123"
    assert action.kind.value == "refund"
