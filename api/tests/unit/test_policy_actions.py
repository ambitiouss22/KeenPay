"""The deterministic policy gate for financial actions.

Pure-function tests, so they can be exhaustive and adversarial rather than
representative. This is the layer where "may this money move?" is answered, and
the cases below are chosen for the ways that answer can be wrong: a limit that
can be approved past, a cap that can be exceeded by exactly one action, a
refund that is checked against the wrong number.
"""

from __future__ import annotations

import pytest

from config.policy import MerchantPolicy
from policy.engine import PolicyEngine
from policy.models import ActionKind, FinancialAction, PolicyOutcome
from policy.rules.action_rules import (
    rule_actor_permitted,
    rule_amount_ceiling,
    rule_daily_cap,
    rule_geography,
    rule_refund_within_capture,
    rule_velocity,
)

POLICY = MerchantPolicy()
ENGINE = PolicyEngine()


def action(**overrides) -> FinancialAction:
    """An unremarkable payment, unless a test says otherwise."""
    base = {
        "kind": ActionKind.PAYMENT,
        "merchant_id": "merchant_keen",
        "amount_paise": 250_000,
        "subject_id": "ord_abc123",
        "actor_id": "user_shopper",
        "actor_role": "shopper",
    }
    return FinancialAction(**{**base, **overrides})


def outcome(**overrides) -> PolicyOutcome:
    return ENGINE.evaluate_action(action(**overrides), policy=POLICY).outcome


# --- the happy path ---------------------------------------------------------


def test_an_ordinary_payment_is_allowed():
    assert outcome() is PolicyOutcome.ALLOW


def test_every_rule_reports_even_when_it_passes():
    """A decision recording only failures cannot prove the cap was checked."""
    decision = ENGINE.evaluate_action(action(), policy=POLICY)
    assert len(decision.rule_results) == decision.metadata["rules_evaluated"]
    assert all(r.passed for r in decision.rule_results)
    assert decision.reasons == []


def test_the_decision_is_deterministic():
    """Same action twice, same verdict - the property auditing depends on."""
    a = action(amount_paise=15_000_000)
    first = ENGINE.evaluate_action(a, policy=POLICY)
    second = ENGINE.evaluate_action(a, policy=POLICY)

    assert first.outcome is second.outcome
    assert first.action_fingerprint == second.action_fingerprint
    assert [r.rule_id for r in first.rule_results] == [r.rule_id for r in second.rule_results]
    assert [r.outcome for r in first.rule_results] == [r.outcome for r in second.rule_results]
    # Only the decision id differs; it identifies the evaluation, not the verdict.
    assert first.decision_id != second.decision_id


# --- amount limits ----------------------------------------------------------


def test_amount_above_the_review_threshold_escalates():
    assert outcome(amount_paise=POLICY.escalate_payment_above_paise + 1) is PolicyOutcome.ESCALATE


def test_amount_exactly_at_the_threshold_does_not_escalate():
    """Thresholds are exclusive. Off-by-one here is a rule nobody can predict."""
    assert outcome(amount_paise=POLICY.escalate_payment_above_paise) is PolicyOutcome.ALLOW


def test_amount_above_the_ceiling_is_denied_not_escalated():
    """A ceiling a human could approve past is a suggestion, not a ceiling."""
    assert outcome(amount_paise=POLICY.max_payment_paise + 1) is PolicyOutcome.DENY


def test_zero_amount_is_denied():
    assert outcome(amount_paise=0) is PolicyOutcome.DENY


def test_a_float_amount_never_reaches_the_rules():
    """StrictInt refuses at construction; truncation would change the amount."""
    with pytest.raises(ValueError):
        action(amount_paise=249.9)


def test_a_negative_amount_never_reaches_the_rules():
    with pytest.raises(ValueError):
        action(amount_paise=-1)


def test_each_kind_has_its_own_ceiling():
    payout = action(kind=ActionKind.PAYOUT, actor_role="admin", amount_paise=60_000_000)
    payment = action(amount_paise=60_000_000)
    # 6 lakh rupees: over the payment ceiling, under the payout ceiling.
    assert rule_amount_ceiling(payout, POLICY).passed
    assert not rule_amount_ceiling(payment, POLICY).passed


# --- who may ask ------------------------------------------------------------


def test_a_shopper_may_pay():
    assert rule_actor_permitted(action(actor_role="shopper"), POLICY).passed


def test_a_shopper_may_not_refund_themselves():
    """The obvious attack, and the reason the actor matrix exists."""
    refund = action(kind=ActionKind.REFUND, actor_role="shopper", captured_paise=999_999)
    assert rule_actor_permitted(refund, POLICY).outcome is PolicyOutcome.DENY


def test_support_may_not_initiate_any_money_movement():
    for kind in ActionKind:
        assert not rule_actor_permitted(
            action(kind=kind, actor_role="support_agent"), POLICY
        ).passed


def test_only_an_admin_may_pay_out():
    for role in ("shopper", "support_agent", "manager", "service"):
        payout = action(kind=ActionKind.PAYOUT, actor_role=role)
        assert not rule_actor_permitted(payout, POLICY).passed
    assert rule_actor_permitted(action(kind=ActionKind.PAYOUT, actor_role="admin"), POLICY).passed


def test_an_unknown_role_matches_nothing_and_is_denied():
    """An allow-list fails closed; a deny-list would have let this through."""
    assert outcome(actor_role="wizard") is PolicyOutcome.DENY


# --- daily cap --------------------------------------------------------------


def test_the_daily_cap_counts_this_action_too():
    """Checking the running total alone lets the cap be exceeded by one action -
    and the last action is the one an attacker gets to choose."""
    at_the_line = action(
        today_total_paise=POLICY.daily_total_cap_paise - 100, amount_paise=100
    )
    over = action(today_total_paise=POLICY.daily_total_cap_paise - 100, amount_paise=101)
    assert rule_daily_cap(at_the_line, POLICY).passed
    assert rule_daily_cap(over, POLICY).outcome is PolicyOutcome.DENY


def test_daily_cap_denial_reports_the_projected_total():
    result = rule_daily_cap(
        action(today_total_paise=POLICY.daily_total_cap_paise, amount_paise=1), POLICY
    )
    assert result.details["projected_paise"] == POLICY.daily_total_cap_paise + 1


# --- velocity ---------------------------------------------------------------


def test_moderate_velocity_escalates_rather_than_refusing():
    """A busy hour is usually a sale. Escalating lets it through with a human."""
    busy = action(actions_last_hour=POLICY.escalate_actions_per_hour_above)
    assert rule_velocity(busy, POLICY).outcome is PolicyOutcome.ESCALATE


def test_extreme_velocity_is_denied():
    runaway = action(actions_last_hour=POLICY.max_actions_per_hour)
    assert rule_velocity(runaway, POLICY).outcome is PolicyOutcome.DENY


def test_quiet_hour_passes_velocity():
    assert rule_velocity(action(actions_last_hour=3), POLICY).passed


# --- refunds ----------------------------------------------------------------


def refund(**overrides) -> FinancialAction:
    base = {
        "kind": ActionKind.REFUND,
        "actor_role": "manager",
        "captured_paise": 100_000,
        "already_refunded_paise": 0,
        "amount_paise": 50_000,
    }
    return action(**{**base, **overrides})


def test_a_partial_refund_within_the_capture_is_fine():
    assert rule_refund_within_capture(refund(), POLICY).passed


def test_a_refund_larger_than_the_capture_is_denied():
    assert (
        rule_refund_within_capture(refund(amount_paise=100_001), POLICY).outcome
        is PolicyOutcome.DENY
    )


def test_earlier_refunds_are_deducted():
    """Without the subtraction the same money leaves twice."""
    second = refund(amount_paise=60_000, already_refunded_paise=50_000)
    assert rule_refund_within_capture(second, POLICY).outcome is PolicyOutcome.DENY


def test_a_fully_refunded_payment_has_nothing_left():
    exhausted = refund(amount_paise=1, already_refunded_paise=100_000)
    assert rule_refund_within_capture(exhausted, POLICY).outcome is PolicyOutcome.DENY


def test_the_capture_rule_ignores_non_refunds():
    assert rule_refund_within_capture(action(), POLICY).passed


def test_refunds_escalate_sooner_than_payments():
    """Money leaving is harder to claw back, so it earns scrutiny earlier."""
    assert POLICY.escalate_refund_above_paise < POLICY.escalate_payment_above_paise
    amount = POLICY.escalate_refund_above_paise + 1
    assert (
        ENGINE.evaluate_action(
            refund(amount_paise=amount, captured_paise=amount), policy=POLICY
        ).outcome
        is PolicyOutcome.ESCALATE
    )


# --- geography --------------------------------------------------------------


def test_a_blocked_country_is_denied():
    policy = MerchantPolicy(blocked_countries=["KP"])
    assert rule_geography(action(buyer_country="KP"), policy).outcome is PolicyOutcome.DENY


def test_a_country_outside_the_usual_markets_escalates():
    assert rule_geography(action(buyer_country="US"), POLICY).outcome is PolicyOutcome.ESCALATE


def test_an_empty_allow_list_means_no_restriction():
    """Failing closed here would stop every transaction the day a field is cleared."""
    policy = MerchantPolicy(allowed_countries=[])
    assert rule_geography(action(buyer_country="BR"), policy).passed


def test_country_matching_is_case_insensitive():
    assert rule_geography(action(buyer_country="in"), POLICY).passed


# --- aggregation ------------------------------------------------------------


def test_deny_beats_escalate():
    """An allow anywhere must never soften a denial - that would be a bypass
    built into the gate's own arithmetic."""
    both = action(
        amount_paise=POLICY.max_payment_paise + 1,  # denies
        actions_last_hour=POLICY.escalate_actions_per_hour_above,  # escalates
    )
    assert ENGINE.evaluate_action(both, policy=POLICY).outcome is PolicyOutcome.DENY


def test_escalate_beats_allow():
    assert outcome(buyer_country="US") is PolicyOutcome.ESCALATE


def test_every_failure_is_reported_not_just_the_first():
    decision = ENGINE.evaluate_action(
        action(amount_paise=POLICY.max_payment_paise + 1, actor_role="wizard"), policy=POLICY
    )
    assert len(decision.reasons) >= 2


# --- fingerprints -----------------------------------------------------------


def test_the_fingerprint_covers_the_amount():
    """Otherwise an approval for a small amount is spendable on a large one."""
    assert action(amount_paise=100).fingerprint() != action(amount_paise=101).fingerprint()


def test_the_fingerprint_covers_the_subject():
    assert action(subject_id="ord_a").fingerprint() != action(subject_id="ord_b").fingerprint()


def test_the_fingerprint_covers_the_kind():
    payment = action()
    refund_same_amount = action(kind=ActionKind.REFUND, actor_role="manager")
    assert payment.fingerprint() != refund_same_amount.fingerprint()


def test_the_fingerprint_covers_the_merchant():
    assert action(merchant_id="a").fingerprint() != action(merchant_id="b").fingerprint()


def test_the_fingerprint_ignores_volatile_context():
    """Velocity counters move between requesting an approval and spending it.
    An approval invalidated by a counter ticking would be useless."""
    quiet = action(actions_last_hour=0, today_total_paise=0)
    busy = action(actions_last_hour=19, today_total_paise=999_999)
    assert quiet.fingerprint() == busy.fingerprint()


def test_the_action_model_refuses_unknown_fields():
    """extra='forbid' - a typo'd field must fail, not be silently ignored."""
    with pytest.raises(ValueError):
        action(amount_pasie=100)
