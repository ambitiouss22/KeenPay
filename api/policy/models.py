"""Policy evaluation models.

Two families live here. The offer models (``ProposedOffer``, ``RuleResult``,
``GuardrailDecision``) judge a negotiated cart. The action models
(``FinancialAction``, ``PolicyDecision``) judge an attempt to move money.

They are kept apart rather than merged into one general "thing being checked",
because the questions differ: an offer is asked "is this price legitimate?",
an action is asked "may this money move, and who has to say so?".
"""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class LineItem(BaseModel):
    sku: str
    product_id: str
    name: str
    quantity: int = Field(ge=1)
    list_unit_price_paise: int = Field(ge=0)
    negotiated_unit_price_paise: int | None = Field(default=None, ge=0)
    cost_paise: int = Field(default=0, ge=0)


class ProposedOffer(BaseModel):
    version: int = Field(ge=1)
    line_items: list[LineItem]
    discount_pct: float = Field(ge=0, le=100)
    discount_amount_paise: int = Field(ge=0)
    subtotal_paise: int = Field(ge=0)
    final_amount_paise: int = Field(ge=0)
    currency: Literal["INR"] = "INR"
    rationale: str = ""


class RuleResult(BaseModel):
    passed: bool
    rule_id: str
    action: Literal["PASS", "CLAMP", "REJECT", "ESCALATE"] = "PASS"
    message: str = ""
    adjusted_offer: ProposedOffer | None = None


class GuardrailDecision(BaseModel):
    decision_id: str
    outcome: Literal["APPROVED", "REJECTED", "ESCALATED"]
    offer_version: int
    approved_offer: ProposedOffer | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    rule_results: list[RuleResult] = Field(default_factory=list)
    policy_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- financial actions ------------------------------------------------------
# Phase 5. The models above describe a *negotiated offer*; the ones below
# describe any *financial action* - a payment, a refund, a payout, a campaign
# spend. They are separate on purpose: an offer is a proposal about a cart, an
# action is an attempt to move money, and the rules that guard them differ.


class ActionKind(str, Enum):
    """What kind of money movement is being attempted.

    A closed set, not a free string. A typo in an action kind must be a
    validation failure, not an action that silently matches no rule and is
    therefore allowed - the classic way a deny-list lets something through.
    """

    PAYMENT = "payment"
    REFUND = "refund"
    PAYOUT = "payout"
    CAMPAIGN_SPEND = "campaign_spend"


class FinancialAction(BaseModel):
    """One attempt to move money, described completely enough to judge it.

    Everything the rules need is a field here. Nothing is read from a clock, a
    database or a global inside rule evaluation, which is what makes a decision
    reproducible: replay the same action and you get the same verdict, forever.
    That property is worth more than the convenience of looking things up
    mid-rule, because it is what lets an auditor re-run a disputed decision.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    merchant_id: str = Field(min_length=1)
    #: Integer paise. StrictInt so a JSON float is refused rather than
    #: truncated into a different amount.
    amount_paise: StrictInt = Field(ge=0)
    currency: Literal["INR"] = "INR"
    #: What the money is about: an order id, a payout id, a campaign id. Part
    #: of the authorization fingerprint, so an approval for one subject cannot
    #: be spent on another.
    subject_id: str = Field(min_length=1)
    #: Who is asking. Never taken from a request body - the routers fill this
    #: from the verified token.
    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)

    # --- context the rules score against, all caller-supplied and explicit ---
    #: Money already moved by this merchant today, in paise.
    today_total_paise: StrictInt = Field(default=0, ge=0)
    #: Actions of this kind in the trailing hour.
    actions_last_hour: StrictInt = Field(default=0, ge=0)
    #: For refunds: what was actually captured, and what has already gone back.
    captured_paise: StrictInt = Field(default=0, ge=0)
    already_refunded_paise: StrictInt = Field(default=0, ge=0)
    #: Risk context.
    buyer_age_days: StrictInt = Field(default=365, ge=0)
    buyer_prior_orders: StrictInt = Field(default=10, ge=0)
    buyer_country: str = "IN"
    ip_country: str = "IN"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        """A stable hash of the parts an approval is granted *for*.

        Only the identifying and financial fields go in. Context like velocity
        counters deliberately does not: those change between requesting an
        authorization and spending it, and an approval that expired because a
        counter ticked would be useless. What must not change is *what* is
        being paid, *how much*, and *to what*.
        """
        canonical = "|".join(
            [
                self.kind.value,
                self.merchant_id,
                str(self.amount_paise),
                self.currency,
                self.subject_id,
            ]
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


class PolicyOutcome(str, Enum):
    """Deny beats escalate beats allow. Always aggregated in that order."""

    ALLOW = "allow"
    ESCALATE = "escalate"
    DENY = "deny"


class ActionRuleResult(BaseModel):
    """One rule's verdict, kept even when it passed.

    A decision that recorded only its failures cannot answer "was the daily cap
    checked at all?" six months later during a dispute. Every rule reports.
    """

    rule_id: str
    outcome: PolicyOutcome = PolicyOutcome.ALLOW
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


class PolicyDecision(BaseModel):
    """The verdict on one financial action.

    Stored verbatim on the authorization record. It is the evidence that a gate
    ran, so it has to be self-contained: the rules that fired, the policy
    version they came from, and the fingerprint of the action they judged.
    """

    decision_id: str
    outcome: PolicyOutcome
    action_kind: ActionKind
    amount_paise: int
    action_fingerprint: str
    reasons: list[str] = Field(default_factory=list)
    rule_results: list[ActionRuleResult] = Field(default_factory=list)
    policy_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW

    @property
    def denied(self) -> bool:
        return self.outcome is PolicyOutcome.DENY
