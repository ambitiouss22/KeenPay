"""Individual guardrail rules."""

from __future__ import annotations

from config.policy import MerchantPolicy
from policy.models import LineItem, ProposedOffer, RuleResult
from utils.money import compute_discount_amount_paise


def rule_max_discount(offer: ProposedOffer, policy: MerchantPolicy) -> RuleResult:
    cap = policy.max_discount_pct
    for item in offer.line_items:
        cap = min(cap, policy.max_discount_pct_per_sku.get(item.sku, cap))
    if offer.discount_pct <= cap:
        return RuleResult(passed=True, rule_id="RULE_MAX_DISCOUNT")
    adjusted = _recalc_offer(offer, discount_pct=cap)
    return RuleResult(
        passed=False,
        rule_id="RULE_MAX_DISCOUNT",
        action="CLAMP",
        message=f"Discount capped at {cap}%",
        adjusted_offer=adjusted,
    )


def rule_max_absolute_discount(offer: ProposedOffer, policy: MerchantPolicy) -> RuleResult:
    if offer.discount_amount_paise <= policy.max_absolute_discount_paise:
        return RuleResult(passed=True, rule_id="RULE_MAX_ABSOLUTE_DISCOUNT")
    cap_pct = (
        (policy.max_absolute_discount_paise / offer.subtotal_paise) * 100
        if offer.subtotal_paise
        else 0
    )
    adjusted = _recalc_offer(offer, discount_pct=min(offer.discount_pct, cap_pct))
    return RuleResult(
        passed=False,
        rule_id="RULE_MAX_ABSOLUTE_DISCOUNT",
        action="CLAMP",
        message="Absolute discount cap applied",
        adjusted_offer=adjusted,
    )


def rule_min_margin(offer: ProposedOffer, policy: MerchantPolicy) -> RuleResult:
    for item in offer.line_items:
        unit = item.negotiated_unit_price_paise or item.list_unit_price_paise
        cost = item.cost_paise
        if cost <= 0:
            continue
        margin_pct = ((unit - cost) / unit) * 100
        if margin_pct < policy.min_margin_pct:
            return RuleResult(
                passed=False,
                rule_id="RULE_MIN_MARGIN",
                action="REJECT",
                message=(
                    f"Margin {margin_pct:.1f}% below floor "
                    f"{policy.min_margin_pct}% for {item.sku}"
                ),
            )
    return RuleResult(passed=True, rule_id="RULE_MIN_MARGIN")


def rule_inventory_bounds(offer: ProposedOffer, policy: MerchantPolicy) -> RuleResult:
    total_qty = sum(i.quantity for i in offer.line_items)
    if total_qty > policy.max_qty_per_order:
        return RuleResult(
            passed=False,
            rule_id="RULE_INVENTORY_BOUNDS",
            action="REJECT",
            message=f"Order quantity {total_qty} exceeds max {policy.max_qty_per_order}",
        )
    for item in offer.line_items:
        if item.quantity > policy.max_qty_per_line:
            return RuleResult(
                passed=False,
                rule_id="RULE_INVENTORY_BOUNDS",
                action="REJECT",
                message=f"Line quantity for {item.sku} exceeds max {policy.max_qty_per_line}",
            )
    return RuleResult(passed=True, rule_id="RULE_INVENTORY_BOUNDS")


def rule_price_sanity(offer: ProposedOffer, _policy: MerchantPolicy) -> RuleResult:
    if offer.final_amount_paise <= 0:
        return RuleResult(
            passed=False,
            rule_id="RULE_PRICE_SANITY",
            action="REJECT",
            message="Invalid final amount",
        )
    for item in offer.line_items:
        unit = item.negotiated_unit_price_paise or item.list_unit_price_paise
        if unit <= 0:
            return RuleResult(
                passed=False,
                rule_id="RULE_PRICE_SANITY",
                action="REJECT",
                message="Invalid unit price",
            )
    return RuleResult(passed=True, rule_id="RULE_PRICE_SANITY")


def rule_currency(offer: ProposedOffer, _policy: MerchantPolicy) -> RuleResult:
    if offer.currency != "INR":
        return RuleResult(
            passed=False, rule_id="RULE_CURRENCY", action="REJECT", message="INR only in v1"
        )
    return RuleResult(passed=True, rule_id="RULE_CURRENCY")


def rule_negotiation_rounds(round_num: int, policy: MerchantPolicy) -> RuleResult:
    if round_num >= policy.max_negotiation_rounds:
        return RuleResult(
            passed=False,
            rule_id="RULE_NEGOTIATION_ROUNDS",
            action="ESCALATE",
            message="Max negotiation rounds reached",
        )
    return RuleResult(passed=True, rule_id="RULE_NEGOTIATION_ROUNDS")


def _recalc_offer(offer: ProposedOffer, *, discount_pct: float) -> ProposedOffer:
    line_items: list[LineItem] = []
    subtotal = 0
    for item in offer.line_items:
        unit = item.list_unit_price_paise
        negotiated = round(unit * (1 - discount_pct / 100))
        line_total = negotiated * item.quantity
        subtotal += line_total
        line_items.append(item.model_copy(update={"negotiated_unit_price_paise": negotiated}))
    discount_amount = compute_discount_amount_paise(
        sum(i.list_unit_price_paise * i.quantity for i in offer.line_items),
        discount_pct,
    )
    return offer.model_copy(
        update={
            "line_items": line_items,
            "discount_pct": discount_pct,
            "discount_amount_paise": discount_amount,
            "subtotal_paise": subtotal,
            "final_amount_paise": subtotal,
        }
    )
