"""Inbound provider event storage and deduplication.

The store is the dedupe. A provider retries until it gets a 2xx, and it is
allowed to retry an event it already delivered, so "have I seen this event id"
has to be answered by something durable rather than by hoping the first
delivery succeeded. :meth:`WebhookRepository.claim` both answers that question
and records the event in one step: a caller cannot check and then forget to
write, which is the shape of every dedupe bug.

The raw payload is kept alongside the parsed result on purpose. When a dispute
arrives months later, the argument is about what the provider actually sent,
not about what our parser made of it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

_MEMORY_EVENTS: dict[str, dict[str, Any]] = {}


def reset_webhooks() -> None:
    """Drop every recorded event. For test isolation only."""
    _MEMORY_EVENTS.clear()


class WebhookRepository:
    """Provider events, keyed by the provider's own event id."""

    async def claim(
        self,
        event_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        signature_valid: bool,
        raw_body: bytes | None = None,
    ) -> bool:
        """Record an event and report whether it is new.

        ``True`` means this caller owns the event and should process it.
        ``False`` means it was already recorded and must not be processed
        again. The write happens with no await between the read and the store,
        so two concurrent deliveries of the same id cannot both win.
        """
        if not event_id:
            # An event with no id cannot be deduplicated, so it is never
            # claimed. Refusing is safer than processing something that would
            # be reprocessed on every retry.
            return False

        if event_id in _MEMORY_EVENTS:
            return False

        _MEMORY_EVENTS[event_id] = {
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
            "raw_body": raw_body.decode("utf-8", "replace") if raw_body else None,
            "signature_valid": signature_valid,
            "processed": False,
            "process_result": None,
            "order_id": None,
            "received_at": datetime.now(UTC),
            "processed_at": None,
        }
        return True

    async def register_event(self, event_id: str) -> bool:
        """Backwards-compatible claim with no payload.

        The checkout session flow called this before the store carried the
        event body. Kept so that path keeps working unchanged.
        """
        return await self.claim(
            event_id, event_type="", payload={}, signature_valid=True, raw_body=None
        )

    async def mark_processed(
        self,
        event_id: str,
        *,
        result: dict[str, Any],
        order_id: str | None = None,
    ) -> None:
        """Record the outcome of processing one event."""
        record = _MEMORY_EVENTS.get(event_id)
        if not record:
            return
        record["processed"] = True
        record["process_result"] = result
        record["order_id"] = order_id
        record["processed_at"] = datetime.now(UTC)

    async def get(self, event_id: str) -> dict[str, Any] | None:
        """Fetch one recorded event."""
        record = _MEMORY_EVENTS.get(event_id)
        return dict(record) if record else None

    async def list_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """Every event that resolved to one order, oldest first."""
        events = [dict(e) for e in _MEMORY_EVENTS.values() if e.get("order_id") == order_id]
        events.sort(key=lambda e: e["received_at"])
        return events

    async def list_unprocessed(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Claimed events that never reached a terminal result."""
        events = [dict(e) for e in _MEMORY_EVENTS.values() if not e.get("processed")]
        events.sort(key=lambda e: e["received_at"])
        return events[:limit]

    async def store_raw(self, event_id: str, payload: dict[str, Any]) -> None:
        """Attach a raw payload to an already-claimed event."""
        record = _MEMORY_EVENTS.get(event_id)
        if record is not None and record.get("raw_body") is None:
            record["raw_body"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = ["WebhookRepository", "reset_webhooks"]
