"""Payment request and response contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreateRequest(BaseModel):
    """Start a payment.

    There is deliberately no amount here. ``extra="forbid"`` means a body that
    tries to smuggle one in is a 422 rather than a silently ignored field, so
    the refusal is visible to whoever sent it.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: str
    authorization_id: str
    idempotency_key: str = Field(..., min_length=16)
    context: dict[str, Any] | None = None


class RefundCreateRequest(BaseModel):
    """Refund part or all of a payment."""

    model_config = ConfigDict(extra="forbid", strict=True)

    amount_paise: int = Field(..., ge=1)
    authorization_id: str
    idempotency_key: str = Field(..., min_length=16)


class PaymentOut(BaseModel):
    """Payment response."""

    id: str
    status: str
    amount_paise: int
    captured_paise: int
    refunded_paise: int
    order_snapshot: dict[str, Any] | None = None
    order_snapshot_hash: str | None = None


class PaymentStatusOut(BaseModel):
    """Status response, with the one bit a caller actually acts on."""

    id: str
    status: str
    settled: bool
