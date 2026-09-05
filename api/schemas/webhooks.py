"""Webhook and reconciliation contracts."""

from typing import Any

from pydantic import BaseModel, Field


class WebhookAck(BaseModel):
    """What the provider gets back for an event we accepted.

    ``received`` is the field the provider's dashboard shows; ``status`` says
    what we actually did with it, which is what makes a delivery log readable
    later without joining against our own records.
    """

    received: bool = True
    status: str
    event_id: str = ""
    order_id: str | None = None


class ReconciliationDiffOut(BaseModel):
    """One disagreement between our ledger and the provider's."""

    payment_id: str
    kind: str
    local: Any = None
    provider: Any = None
    detail: str = ""


class ReconciliationRunOut(BaseModel):
    """The result of one reconciliation pass."""

    run_id: str
    merchant_id: str
    checked: int
    resolved: int
    resolved_captured: int
    resolved_failed: int
    still_unknown: int
    unreachable: int = 0
    clean: bool
    diffs: list[ReconciliationDiffOut] = Field(default_factory=list)


class ReconciliationStatusOut(BaseModel):
    """The current reconciliation picture for one merchant."""

    merchant_id: str
    unknown_payments: int
    healthy: bool
    last_run: dict[str, Any] | None = None
