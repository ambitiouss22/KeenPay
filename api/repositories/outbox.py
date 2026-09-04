"""Transactional event outbox."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_OUTBOX: dict[str, dict[str, Any]] = {}


def reset_outbox() -> None:
    """Clear outbox. For test isolation."""
    _OUTBOX.clear()


class OutboxRepository:
    """Append-only event outbox."""

    async def emit(
        self,
        aggregate_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Record an event."""
        event_id = str(uuid4())
        _OUTBOX[event_id] = {
            "id": event_id,
            "aggregate_id": aggregate_id,
            "event_type": event_type,
            "data": data,
            "published": False,
            "created_at": datetime.now(UTC),
        }

    async def events_for(self, aggregate_id: str) -> list[dict[str, Any]]:
        """Get all events for aggregate."""
        return [
            dict(e) for e in _OUTBOX.values()
            if e.get("aggregate_id") == aggregate_id
        ]

    async def unpublished(self) -> list[dict[str, Any]]:
        """Get unpublished events."""
        return [
            dict(e) for e in _OUTBOX.values()
            if not e.get("published")
        ]

    async def mark_published(self, event_id: str) -> None:
        """Mark event as published."""
        if event_id in _OUTBOX:
            _OUTBOX[event_id]["published"] = True
