"""PolicyEngine - synchronous guardrail evaluation (no LLM).

Two entry points, one engine.

``evaluate`` judges a negotiated offer: discounts, margin, inventory. It is the
Phase 2 surface and is unchanged.

``evaluate_action`` judges a financial action - a payment, refund, payout or
campaign spend - and answers allow, escalate or deny. It is the first of the
three Phase 5 gates, and everything that moves money passes through it before
risk scoring and authorization.

Both are deterministic and free of I/O beyond loading the merchant's policy. No
language model is consulted on either path. A model in the approve path would
mean the answer to "may this money move?" could differ between two identical
requests, which is not a property a payment system may have.
"""

from __future__ import annotations

from uuid import uuid4

from config.policy import MerchantPolicy, load_merchant_policy
from policy.anomaly import anomaly_score, detect_injection
from policy.models import (
    ActionRuleResult,
    FinancialAction,
    GuardrailDecision,
    PolicyDecision,
    PolicyOutcome,
    ProposedOffer,
    RuleResult,
)
from policy.rules.action_rules import ACTION_RULES
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

    # --- financial actions (phase 5) ---------------------------------------

    def evaluate_action(
        self,
        action: FinancialAction,
        *,
        policy: MerchantPolicy | None = None,
    ) -> PolicyDecision:
        """Judge one attempt to move money.

        Runs every rule - not just up to the first failure. Short-circuiting
        would be faster and would produce a decision record that names one
        reason when there were four, which turns "fix the problem" into four
        round trips. Cost is a handful of comparisons; the rules touch nothing.

        Aggregation is deny beats escalate beats allow, unconditionally. A
        human may approve past an *escalation*; nobody may approve past a
        *denial*, which is the difference between the two outcomes. Aggregating
        the other way - letting an allow anywhere soften a deny - is how a
        gate ends up with a bypass built into its own arithmetic.

        ``policy`` is injectable so tests and per-merchant overrides do not
        have to reach through a module-level cache.
        """
        policy = policy or load_merchant_policy(action.merchant_id)

        results: list[ActionRuleResult] = [rule(action, policy) for rule in ACTION_RULES]
        outcome = self._aggregate_action(results)
        reasons = [r.message for r in results if not r.passed and r.message]

        return PolicyDecision(
            decision_id=str(uuid4()),
            outcome=outcome,
            action_kind=action.kind,
            amount_paise=action.amount_paise,
            action_fingerprint=action.fingerprint(),
            reasons=reasons,
            rule_results=results,
            policy_version=policy.policy_version,
            metadata={
                "merchant_id": action.merchant_id,
                "actor_role": action.actor_role,
                "subject_id": action.subject_id,
                "rules_evaluated": len(results),
            },
        )

    @staticmethod
    def _aggregate_action(results: list[ActionRuleResult]) -> PolicyOutcome:
        """Fail closed, and in a fixed order.

        Written as two explicit passes rather than a max() over an ordered
        enum. The ordering of an enum is an implementation detail that a later
        edit can reorder without anyone noticing; this cannot be reordered by
        accident.
        """
        if any(r.outcome is PolicyOutcome.DENY for r in results):
            return PolicyOutcome.DENY
        if any(r.outcome is PolicyOutcome.ESCALATE for r in results):
            return PolicyOutcome.ESCALATE
        return PolicyOutcome.ALLOW


#: Shared instance. The engine holds no mutable state - every call reads its
#: policy and returns a new decision - so one instance is safe across requests.
policy_engine = PolicyEngine()
