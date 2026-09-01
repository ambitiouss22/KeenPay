"""Policy evaluation models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
