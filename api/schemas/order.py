"""Order API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrderOut(BaseModel):
    id: str
    session_id: str
    status: str
    final_amount_paise: int
    currency: str = "INR"
    razorpay_payment_link_id: str | None = None
    razorpay_payment_id: str | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    guardrail_decision_id: str
    offer_version: int
    created_at: datetime | None = None
    paid_at: datetime | None = None
