"""Audit ledger contracts."""

from typing import Any

from pydantic import BaseModel, Field


class LedgerEntryOut(BaseModel):
    """One entry, with the hash that binds it to the entry before it."""

    seq: int
    merchant_id: str
    entity_type: str
    entity_id: str
    actor: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: str
    prev_hash: str
    entry_hash: str
    correlation_id: str | None = None


class LedgerPageOut(BaseModel):
    """A window onto a merchant's chain.

    ``head_hash`` is returned on every page on purpose: it is the one value a
    caller needs to keep in order to detect later tampering, and making it
    incidental to pagination is how nobody ends up keeping it.
    """

    entries: list[LedgerEntryOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    head_hash: str


class ChainVerificationOut(BaseModel):
    """The result of walking a whole chain."""

    merchant_id: str
    valid: bool
    entry_count: int
    head_hash: str
    errors: list[str] = Field(default_factory=list)
