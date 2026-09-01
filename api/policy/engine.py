"""PolicyEngine — synchronous guardrail evaluation (no LLM)."""

from __future__ import annotations

from uuid import uuid4

from config.policy import MerchantPolicy, load_merchant_policy
from policy.anomaly import anomaly_score, detect_injection
from policy.models import GuardrailDecision, ProposedOffer, RuleResult
from policy.rules.evaluators import (
    rule_currency,
    rule_inventory_bounds,
    rule_max_absolute_discount,
    rule_max_discount,
    rule_min_margin,
    rule_negotiation_rounds,
    rule_price_sanity,
)


class PolicyEngine:
    def evaluate(
        self,
        *,
        offer: ProposedOffer,
        merchant_id: str,
        negotiation_round: int = 0,
        user_text: str = "",
        stock_available: dict[str, int] | None = None,
    ) -> GuardrailDecision:
        policy = load_merchant_policy(merchant_id)
        stock_available = stock_available or {}
        results: list[RuleResult] = []

        injected, flags = detect_injection(user_text)
        score = anomaly_score(user_text, flags)
        if injected:
            results.append(
                RuleResult(
                    passed=False,
                    rule_id="RULE_PROMPT_INJECTION",
                    action="REJECT",
                    message="Suspicious input detected",
                )
            )
        if score >= policy.block_on_anomaly_score_gte:
            results.append(
                RuleResult(
                    passed=False,
                    rule_id="RULE_SECURITY_ANOMALY",
                    action="ESCALATE",
                    message=f"Anomaly score {score:.2f}",
                )
            )

        working_offer = offer
        for rule_fn in (
            rule_max_discount,
            rule_max_absolute_discount,
            rule_min_margin,
            rule_inventory_bounds,
            rule_price_sanity,
            rule_currency,
        ):
            result = rule_fn(working_offer, policy)
            results.append(result)
            if result.adjusted_offer:
                working_offer = result.adjusted_offer

        results.append(rule_negotiation_rounds(negotiation_round, policy))

        for item in working_offer.line_items:
            available = stock_available.get(item.sku, 0)
            if item.quantity > available:
                results.append(
                    RuleResult(
                        passed=False,
                        rule_id="RULE_INVENTORY_AVAILABLE",
                        action="REJECT",
                        message=f"Insufficient stock for {item.sku}",
                    )
                )
                break
        else:
            results.append(RuleResult(passed=True, rule_id="RULE_INVENTORY_AVAILABLE"))

        outcome = self._aggregate(results)
        rejection_reasons = [r.message for r in results if not r.passed and r.message]

        return GuardrailDecision(
            decision_id=str(uuid4()),
            outcome=outcome,
            offer_version=working_offer.version,
            approved_offer=working_offer if outcome == "APPROVED" else None,
            rejection_reasons=rejection_reasons,
            rule_results=results,
            policy_version=policy.policy_version,
            metadata={"anomaly_score": score, "injection_flags": flags},
        )

    def _aggregate(self, results: list[RuleResult]) -> str:
        if any(r.rule_id == "RULE_SECURITY_ANOMALY" and not r.passed for r in results):
            return "ESCALATED"
        if any(r.action == "ESCALATE" for r in results):
            return "ESCALATED"
        if any(r.action == "REJECT" for r in results):
            return "REJECTED"
        return "APPROVED"
