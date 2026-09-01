"""Redis trace pub/sub — in-memory fallback for dev."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from config.settings import get_settings

_MEMORY_TRACES: dict[str, list[dict[str, Any]]] = {}


class TraceService:
    async def publish(self, session_id: str, event_type: str, *, node_name: str | None = None, payload: dict | None = None) -> str:
        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "node_name": node_name,
            "payload": payload or {},
        }
        _MEMORY_TRACES.setdefault(session_id, []).append(event)

        if not get_settings().use_in_memory_store:
            try:
                from dependencies.redis import get_redis

                redis = await get_redis()
                import json

                await redis.publish(f"trace:{session_id}", json.dumps(event))
            except Exception:
                pass
        return event_id

    def get_buffer(self, session_id: str) -> list[dict[str, Any]]:
        return list(_MEMORY_TRACES.get(session_id, []))
