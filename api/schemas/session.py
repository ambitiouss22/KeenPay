"""Session API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    merchant_id: str = "merchant_keen"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    created_at: datetime
    ws_url: str


class SessionOut(BaseModel):
    session_id: str
    status: str
    negotiation_round: int = 0
    proposed_offer: dict | None = None
    approved_offer: dict | None = None
    guardrail_decision: str | None = None
    final_amount_paise: int | None = None
    order_id: str | None = None
    payment_link_url: str | None = None


class ChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatMessageResponse(BaseModel):
    message_id: str
    role: str
    text: str
    structured: dict | None = None
    trace_event_ids: list[str] = Field(default_factory=list)


class ConfirmPaymentRequest(BaseModel):
    confirmed: bool = True
    idempotency_key: str


class ConfirmPaymentResponse(BaseModel):
    session_id: str
    order_id: str
    payment_link_id: str
    payment_link_url: str
    final_amount_paise: int
    currency: str = "INR"
    expires_at: datetime | None = None
