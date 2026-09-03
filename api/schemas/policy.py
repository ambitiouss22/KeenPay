"""Wire shapes for the policy, risk and authorization endpoints.

Two rules govern everything in this file.

**Money is ``StrictInt`` paise.** Pydantic will happily coerce ``249.9`` into an
``int`` field by truncation, turning one amount into a different amount with no
error anywhere. Every paise field here refuses a float at the edge.

**Identity is never a body field.** There is no ``merchant_id`` and no
``actor_id`` on any request model. Both come from the verified token in the
router. A body that could name its own merchant is a cross-tenant write waiting
to be discovered, and a body that could name its own actor makes the four-eyes
rule unenforceable - the requester would simply claim to be somebody else.

The context fields (velocity counters, buyer history, geography) *are* accepted
from the caller, because in this phase the caller is the internal service that
holds them. They are inputs to a score, never to a permission: the worst a
dishonest context can do is inflate the number of approvals required, which
fails safe. Phase 6 moves them server-side when the counters exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

ActionKindLiteral = Literal["payment", "refund", "payout", "campaign_spend"]


class ActionContext(BaseModel):
    """Everything the rules and the scorer read, other than the action itself.

    Defaults describe an unremarkable transaction by an established buyer, so a
    caller that supplies nothing gets a clean baseline rather than an accidental
    high-risk score from zeroed history.
    """

    model_config = ConfigDict(extra="forbid")

    today_total_paise: StrictInt = Field(default=0, ge=0)
    actions_last_hour: StrictInt = Field(default=0, ge=0)
    captured_paise: StrictInt = Field(default=0, ge=0)
    already_refunded_paise: StrictInt = Field(default=0, ge=0)
    buyer_age_days: StrictInt = Field(default=365, ge=0)
    buyer_prior_orders: StrictInt = Field(default=10, ge=0)
    buyer_country: str = Field(default="IN", min_length=2, max_length=2)
    ip_country: str = Field(default="IN", min_length=2, max_length=2)


class ActionRequest(BaseModel):
    """One financial action, as a caller describes it."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKindLiteral
    amount_paise: StrictInt = Field(ge=0)
    subject_id: str = Field(min_length=1, max_length=128)
    currency: Literal["INR"] = "INR"
    context: ActionContext = Field(default_factory=ActionContext)


class RuleResultOut(BaseModel):
    rule_id: str
    outcome: Literal["allow", "escalate", "deny"]
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class PolicyDecisionOut(BaseModel):
    decision_id: str
    outcome: Literal["allow", "escalate", "deny"]
    action_kind: ActionKindLiteral
    amount_paise: int
    action_fingerprint: str
    reasons: list[str] = Field(default_factory=list)
    rule_results: list[RuleResultOut] = Field(default_factory=list)
    policy_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskOut(BaseModel):
    score: float
    band: Literal["low", "medium", "high"]
    signals: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluateResponse(BaseModel):
    """A dry run of the gate.

    Carries what *would* happen without creating anything. Useful for a UI that
    wants to warn "this will need two approvals" before the operator commits,
    and for testing a policy change against real traffic shapes.

    ``risk`` is null when policy denied: a denial is categorical and the scorer
    is not consulted, so reporting a score would imply a weighing that did not
    happen.
    """

    decision: PolicyDecisionOut
    risk: RiskOut | None = None
    required_approvals: int
    would_auto_approve: bool


class ApproverOut(BaseModel):
    approver_id: str
    role: str
    at: datetime


class AuthorizationOut(BaseModel):
    """An authorization record as the API reports it.

    ``policy_decision`` and ``risk`` are included in full rather than
    summarised. This record is the audit trail for a money movement, and a
    summary is the thing you discover is missing a field on the day you need
    it in a dispute.
    """

    id: str
    merchant_id: str
    action_kind: ActionKindLiteral
    amount_paise: int
    currency: str
    subject_id: str
    action_fingerprint: str
    requested_by: str
    requested_by_role: str
    status: Literal["pending", "approved", "denied", "consumed", "expired", "revoked"]
    required_approvals: int
    approvers: list[ApproverOut] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime | None = None
    approved_at: datetime | None = None
    consumed_at: datetime | None = None

    @property
    def approvals_outstanding(self) -> int:
        return max(0, self.required_approvals - len(self.approvers))


class AuthorizationApproveRequest(BaseModel):
    """The approver's identity is not here - it comes from their token.

    A note is optional and is the approver's own words about why they approved.
    It is stored, never parsed: nothing branches on it.
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class RefundEligibilityOut(BaseModel):
    eligible: bool
    max_refundable_paise: int
    reasons: list[str] = Field(default_factory=list)
    requires_authorization: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ActionContext",
    "ActionKindLiteral",
    "ActionRequest",
    "ApproverOut",
    "AuthorizationApproveRequest",
    "AuthorizationOut",
    "PolicyDecisionOut",
    "PolicyEvaluateResponse",
    "RefundEligibilityOut",
    "RiskOut",
    "RuleResultOut",
]
