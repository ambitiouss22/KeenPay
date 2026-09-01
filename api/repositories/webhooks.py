"""Webhook event idempotency store."""

from __future__ import annotations

from typing import Any

from config.settings import get_settings

_MEMORY_EVENTS: set[str] = set()


class WebhookRepository:
    def __init__(self) -> None:
        self._memory = get_settings().use_in_memory_store

    async def register_event(self, event_id: str) -> bool:
        """Return True if new event, False if duplicate."""
        if event_id in _MEMORY_EVENTS:
            return False
        _MEMORY_EVENTS.add(event_id)
        return True

    async def store_raw(self, event_id: str, payload: dict[str, Any]) -> None:
        _ = payload  # persisted to webhook_events in full PG implementation
