"""Opportunity schemas.

Note what a request cannot say. There is no score field, no price field and no
discount field on ``OpportunityHint``: a recommendation may name a kind and a
sku, and nothing else. Everything numeric on the way out is computed by the
rules from the merchant's own catalogue.

That shape is the point rather than an oversight. The AI Runtime is the expected
sender of these hints, and it processes untrusted text for a living; the safe
design is one where the worst a compromised sender can do is ask for a different
product to be *looked at*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OpportunityKind = Literal["upsell", "cross_sell"]


class OpportunityHint(BaseModel):
    """One recommendation from a reasoning component.

    Advisory. It is re-scored by the same rules as everything else, and dropped
    if those rules would not have produced it.
    """

    kind: OpportunityKind
    sku: str = Field(min_length=1, max_length=64)


class OpportunityGenerateRequest(BaseModel):
    #: Suggest around this cart's lines. Absent means suggest around the
    #: merchant's highest-priced sellable products instead.
    cart_id: str | None = Field(default=None, max_length=64)
    kinds: list[OpportunityKind] | None = None
    max_suggestions: int = Field(default=10, ge=1, le=50)
    recommendations: list[OpportunityHint] = Field(default_factory=list, max_length=20)


class RejectedHint(BaseModel):
    kind: str
    sku: str
    reason: str


class OpportunityOut(BaseModel):
    id: str
    kind: str
    subject_id: str | None = None
    sku: str | None = None
    name: str | None = None
    #: The catalogue's price for the suggested item. Not a quote and not an
    #: offer - acting on a suggestion still goes through the campaign path.
    list_price_paise: int | None = None
    anchor_sku: str | None = None
    rationale: str | None = None
    score: float | None = None
    acted_on: bool = False
    created_at: datetime | None = None


class OpportunityGenerateResponse(BaseModel):
    subject_id: str
    items: list[OpportunityOut] = Field(default_factory=list)
    generated: int = 0
    #: Recommendations the rules refused, with the reason. Returned rather than
    #: dropped: a silently ignored hint is indistinguishable from one that never
    #: arrived, and the sender needs to be able to tell the difference.
    rejected: list[RejectedHint] = Field(default_factory=list)


class OpportunityListResponse(BaseModel):
    items: list[OpportunityOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


__all__ = [
    "OpportunityGenerateRequest",
    "OpportunityGenerateResponse",
    "OpportunityHint",
    "OpportunityKind",
    "OpportunityListResponse",
    "OpportunityOut",
    "RejectedHint",
]
