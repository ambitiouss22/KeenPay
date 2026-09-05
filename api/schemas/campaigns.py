"""Campaign and budget schemas.

Money crosses this boundary only as integer paise, and only as ``StrictInt``.
Pydantic would otherwise coerce ``1000.7`` into ``1000`` without complaint,
turning a budget into a different budget - which is precisely the class of
silent error a hard cap exists to rule out.

There is deliberately no field anywhere here for raising a budget. A campaign's
cap is set when it is opened and is not editable through the API; the only
numbers that move afterwards are ``reserved`` and ``spent``, and both move
through the reservation path where they are recorded in the ledger.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, StrictInt


class BudgetOut(BaseModel):
    """The three counters and what they imply."""

    campaign_id: str
    budget_paise: int
    reserved_paise: int
    spent_paise: int
    remaining_paise: int
    exhausted: bool


class CampaignOut(BaseModel):
    id: str
    name: str
    code: str | None = None
    budget_paise: int
    reserved_paise: int
    spent_paise: int
    remaining_paise: int
    max_discount_pct: Decimal | None = None
    active: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    created_at: datetime | None = None


class CampaignListResponse(BaseModel):
    items: list[CampaignOut] = Field(default_factory=list)
    total: int = 0


class CampaignCreateRequest(BaseModel):
    """Opening a campaign. The merchant comes from the token, never the body."""

    name: str = Field(min_length=1, max_length=255)
    budget_paise: StrictInt = Field(ge=1)
    code: str | None = Field(default=None, max_length=64)
    #: Per-order ceiling as a percentage of the order. ``None`` means the budget
    #: is the only limit. Carried as ``Decimal`` so ``12.5`` stays ``12.5``.
    max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class BudgetMoveRequest(BaseModel):
    """A reservation or a release. Both name an amount and, usually, an order."""

    amount_paise: StrictInt = Field(ge=1)
    order_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=500)


class ReserveRequest(BudgetMoveRequest):
    """A reservation additionally carries a key.

    Required, not optional. A reserve without one is a reserve that doubles on
    retry, and a client that has to opt in to safety will forget.
    """

    idempotency_key: str = Field(min_length=8, max_length=128)


class ReserveOut(BaseModel):
    campaign_id: str
    reserved_paise: int
    order_id: str | None = None
    budget: BudgetOut


class ReleaseOut(BaseModel):
    campaign_id: str
    released_paise: int
    order_id: str | None = None
    budget: BudgetOut


__all__ = [
    "BudgetMoveRequest",
    "BudgetOut",
    "CampaignCreateRequest",
    "CampaignListResponse",
    "CampaignOut",
    "ReleaseOut",
    "ReserveOut",
    "ReserveRequest",
]
