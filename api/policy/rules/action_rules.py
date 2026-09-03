"""Deterministic rules for financial actions.

Every function here is pure: it takes the action and the merchant policy and
returns a verdict. No clock, no database, no globals. That is not stylistic
tidiness - it is what makes a decision reproducible. When a merchant disputes a
refusal eight months later, the only way to answer honestly is to replay the
exact inputs and get the exact same verdict.

Each rule reports even when it passes. A decision record that listed only the
failures could not answer "was the daily cap checked?", and "we have no
evidence it ran" is indistinguishable from "it did not run".

Rules never mutate the action. A rule that quietly clamped an amount would
produce a decision approving a number the caller never asked for, and the
caller would go on to charge its own, larger figure.
"""

from __future__ import annotations

from config.policy import MerchantPolicy
from policy.models import ActionKind, ActionRuleResult, FinancialAction, PolicyOutcome

#: Which roles may even attempt each kind of money movement.
#:
#: An allow-list, not a deny-list. An unknown role matches nothing and is
#: refused; with a deny-list, a role added later would be permitted everything
#: until someone remembered to forbid it.
ACTOR_MATRIX: dict[ActionKind, frozenset[str]] = {
    ActionKind.PAYMENT: frozenset({"shopper", "service", "manager", "admin"}),
    ActionKind.REFUND: frozenset({"service", "manager", "admin"}),
    # Payouts move money out to a bank account. Admin only.
    ActionKind.PAYOUT: frozenset({"admin"}),
    ActionKind.CAMPAIGN_SPEND: frozenset({"manager", "admin"}),
}

#: Hard ceiling per kind, read off the policy.
_CEILING_FIELD: dict[ActionKind, str] = {
    ActionKind.PAYMENT: "max_payment_paise",
    ActionKind.REFUND: "max_refund_paise",
    ActionKind.PAYOUT: "max_payout_paise",
    ActionKind.CAMPAIGN_SPEND: "max_campaign_spend_paise",
}

#: Escalation threshold per kind. A kind absent from this map never escalates
#: on amount alone - it is governed by its ceiling and by risk.
_ESCALATE_FIELD: dict[ActionKind, str] = {
    ActionKind.PAYMENT: "escalate_payment_above_paise",
    ActionKind.REFUND: "escalate_refund_above_paise",
}


def _allow(rule_id: str, message: str = "", **details) -> ActionRuleResult:
    return ActionRuleResult(
        rule_id=rule_id, outcome=PolicyOutcome.ALLOW, message=message, details=details
    )


def _deny(rule_id: str, message: str, **details) -> ActionRuleResult:
    return ActionRuleResult(
        rule_id=rule_id, outcome=PolicyOutcome.DENY, message=message, details=details
    )


def _escalate(rule_id: str, message: str, **details) -> ActionRuleResult:
    return ActionRuleResult(
        rule_id=rule_id, outcome=PolicyOutcome.ESCALATE, message=message, details=details
    )


# --- rules ------------------------------------------------------------------


def rule_currency(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """One currency per merchant, checked rather than assumed.

    The type system already restricts this to INR today. The rule stays because
    the day a second currency is added, an unchecked mixed-currency comparison
    of ``amount_paise`` against a rupee limit is a silent factor-of-a-hundred
    error, and this is where that gets caught.
    """
    if action.currency != policy.currency:
        return _deny(
            "ACTION_CURRENCY",
            f"{action.currency} is not accepted by this merchant",
            expected=policy.currency,
            got=action.currency,
        )
    return _allow("ACTION_CURRENCY")


def rule_amount_positive(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """A money movement of zero is not a money movement.

    Refusing it is not pedantry: a zero-amount action that reaches an
    authorization record produces an approval whose fingerprint covers no
    money, which is exactly the shape an attacker wants when hunting for an
    approval to reuse.
    """
    if action.amount_paise <= 0:
        return _deny(
            "ACTION_AMOUNT_POSITIVE",
            "amount must be greater than zero",
            amount_paise=action.amount_paise,
        )
    return _allow("ACTION_AMOUNT_POSITIVE")


def rule_amount_ceiling(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Refuse anything above the hard cap for this kind.

    Deny, never escalate. A ceiling that a human could approve past is a
    suggestion, and the point of the ceiling is that no single approval - or
    single compromised approver - can move an unbounded amount.
    """
    field = _CEILING_FIELD.get(action.kind)
    if field is None:  # pragma: no cover - ActionKind is closed and fully mapped
        return _deny("ACTION_AMOUNT_CEILING", f"no ceiling configured for {action.kind.value}")

    ceiling = getattr(policy, field)
    if action.amount_paise > ceiling:
        return _deny(
            "ACTION_AMOUNT_CEILING",
            f"{action.kind.value} of {action.amount_paise} paise exceeds the "
            f"ceiling of {ceiling} paise",
            amount_paise=action.amount_paise,
            ceiling_paise=ceiling,
        )
    return _allow("ACTION_AMOUNT_CEILING", ceiling_paise=ceiling)


def rule_amount_escalation(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Send large-but-legal amounts to a human instead of refusing them."""
    field = _ESCALATE_FIELD.get(action.kind)
    if field is None:
        return _allow("ACTION_AMOUNT_ESCALATION", "kind has no amount escalation threshold")

    threshold = getattr(policy, field)
    if action.amount_paise > threshold:
        return _escalate(
            "ACTION_AMOUNT_ESCALATION",
            f"{action.kind.value} of {action.amount_paise} paise is above the "
            f"review threshold of {threshold} paise",
            amount_paise=action.amount_paise,
            threshold_paise=threshold,
        )
    return _allow("ACTION_AMOUNT_ESCALATION", threshold_paise=threshold)


def rule_daily_cap(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Cap what one merchant can move in a day, cumulatively.

    Checked against the total *including* this action. Checking the running
    total alone would let the cap be exceeded by exactly the size of the last
    action - and the last action is the one an attacker chooses.
    """
    projected = action.today_total_paise + action.amount_paise
    if projected > policy.daily_total_cap_paise:
        return _deny(
            "ACTION_DAILY_CAP",
            f"this action would bring today's total to {projected} paise, over "
            f"the daily cap of {policy.daily_total_cap_paise} paise",
            today_total_paise=action.today_total_paise,
            projected_paise=projected,
            cap_paise=policy.daily_total_cap_paise,
        )
    return _allow("ACTION_DAILY_CAP", projected_paise=projected)


def rule_velocity(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Rate-limit money movement, with review before refusal.

    Two thresholds. A burst of activity is usually a sale, occasionally a
    compromised key; escalating first lets the legitimate case through with a
    human's blessing instead of failing an honest merchant's busiest hour.
    """
    count = action.actions_last_hour
    if count >= policy.max_actions_per_hour:
        return _deny(
            "ACTION_VELOCITY",
            f"{count} {action.kind.value} actions in the last hour exceeds the "
            f"limit of {policy.max_actions_per_hour}",
            actions_last_hour=count,
            limit=policy.max_actions_per_hour,
        )
    if count >= policy.escalate_actions_per_hour_above:
        return _escalate(
            "ACTION_VELOCITY",
            f"{count} {action.kind.value} actions in the last hour is above the "
            f"review threshold of {policy.escalate_actions_per_hour_above}",
            actions_last_hour=count,
            threshold=policy.escalate_actions_per_hour_above,
        )
    return _allow("ACTION_VELOCITY", actions_last_hour=count)


def rule_actor_permitted(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Only certain roles may attempt certain kinds of movement.

    This duplicates the permission check the router already made, on purpose.
    The router guards one entry point; this guards the decision itself, so a
    background job or a future internal caller that never passes through a
    router is judged by the same rule.
    """
    allowed = ACTOR_MATRIX.get(action.kind, frozenset())
    if action.actor_role not in allowed:
        return _deny(
            "ACTION_ACTOR_PERMITTED",
            f"role '{action.actor_role}' may not initiate a {action.kind.value}",
            role=action.actor_role,
            allowed=sorted(allowed),
        )
    return _allow("ACTION_ACTOR_PERMITTED", role=action.actor_role)


def rule_refund_within_capture(
    action: FinancialAction, policy: MerchantPolicy
) -> ActionRuleResult:
    """A refund may not exceed what is left of what was captured.

    The subtraction is the whole rule: refunding against the *captured* amount
    without deducting earlier refunds is how the same money leaves twice.
    """
    if action.kind is not ActionKind.REFUND:
        return _allow("ACTION_REFUND_WITHIN_CAPTURE", "not a refund")

    refundable = action.captured_paise - action.already_refunded_paise
    if refundable <= 0:
        return _deny(
            "ACTION_REFUND_WITHIN_CAPTURE",
            "nothing left to refund on this payment",
            captured_paise=action.captured_paise,
            already_refunded_paise=action.already_refunded_paise,
        )
    if action.amount_paise > refundable:
        return _deny(
            "ACTION_REFUND_WITHIN_CAPTURE",
            f"refund of {action.amount_paise} paise exceeds the {refundable} "
            "paise still refundable",
            amount_paise=action.amount_paise,
            refundable_paise=refundable,
        )
    return _allow("ACTION_REFUND_WITHIN_CAPTURE", refundable_paise=refundable)


def rule_geography(action: FinancialAction, policy: MerchantPolicy) -> ActionRuleResult:
    """Refuse blocked countries; send unfamiliar ones to review.

    An empty allow list means "no geographic restriction", not "allow nothing".
    The distinction matters because an unconfigured merchant must be able to
    trade, and a policy that fails closed on an empty list would silently stop
    every transaction the first time someone cleared the field.
    """
    country = (action.buyer_country or "").upper()
    blocked = {c.upper() for c in policy.blocked_countries}
    allowed = {c.upper() for c in policy.allowed_countries}

    if country in blocked:
        return _deny("ACTION_GEOGRAPHY", f"{country} is a blocked country", country=country)
    if allowed and country not in allowed:
        return _escalate(
            "ACTION_GEOGRAPHY",
            f"{country} is outside this merchant's usual markets",
            country=country,
            allowed=sorted(allowed),
        )
    return _allow("ACTION_GEOGRAPHY", country=country)


#: Evaluated in this order. Order does not change the verdict - the aggregation
#: is deny-beats-escalate-beats-allow regardless - but it fixes the order rules
#: appear in the decision record, which makes two records comparable by eye.
ACTION_RULES = (
    rule_currency,
    rule_amount_positive,
    rule_actor_permitted,
    rule_amount_ceiling,
    rule_amount_escalation,
    rule_refund_within_capture,
    rule_daily_cap,
    rule_velocity,
    rule_geography,
)


__all__ = [
    "ACTION_RULES",
    "ACTOR_MATRIX",
    "rule_actor_permitted",
    "rule_amount_ceiling",
    "rule_amount_escalation",
    "rule_amount_positive",
    "rule_currency",
    "rule_daily_cap",
    "rule_geography",
    "rule_refund_within_capture",
    "rule_velocity",
]
