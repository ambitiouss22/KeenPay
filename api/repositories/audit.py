"""Append-only audit log persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

_MEMORY_AUDIT: list[dict[str, Any]] = []


class AuditRepository:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    async def append(
        self,
        *,
        session_id: str | None,
        order_id: str | None,
        merchant_id: str,
        actor: str,
        action: str,
        decision_id: str | None = None,
        offer_version: int | None = None,
        idempotency_key: str | None = None,
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        trace_metadata: dict | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "session_id": session_id,
            "order_id": order_id,
            "merchant_id": merchant_id,
            "actor": actor,
            "action": action,
            "decision_id": decision_id,
            "offer_version": offer_version,
            "idempotency_key": idempotency_key,
            "input_snapshot": input_snapshot or {},
            "output_snapshot": output_snapshot or {},
            "trace_metadata": trace_metadata or {},
            "created_at": datetime.now(UTC),
        }
        if self._memory:
            _MEMORY_AUDIT.append(record)
            return record

        assert self._session is not None
        sql = text(
            """
            INSERT INTO audit_logs (
                session_id, order_id, merchant_id, actor, action,
                decision_id, offer_version, idempotency_key,
                input_snapshot, output_snapshot, trace_metadata
            ) VALUES (
                :session_id::uuid, :order_id, :merchant_id, :actor::audit_actor, :action,
                :decision_id::uuid, :offer_version, :idempotency_key,
                :input_snapshot::jsonb, :output_snapshot::jsonb, :trace_metadata::jsonb
            ) RETURNING id::text
            """
        )
        await self._session.execute(
            sql,
            {
                **record,
                "input_snapshot": json.dumps(record["input_snapshot"]),
                "output_snapshot": json.dumps(record["output_snapshot"]),
                "trace_metadata": json.dumps(record["trace_metadata"]),
            },
        )
        return record

    async def list_for_session(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        if self._memory:
            items = [a for a in _MEMORY_AUDIT if a["session_id"] == session_id]
            items.sort(key=lambda x: x["created_at"], reverse=True)
            total = len(items)
            return items[offset : offset + limit], total

        assert self._session is not None
        total = (
            await self._session.execute(
                text("SELECT COUNT(*) FROM audit_logs WHERE session_id = :sid::uuid"),
                {"sid": session_id},
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id::text, actor, action, decision_id::text, offer_version,
                           input_snapshot, output_snapshot, created_at
                    FROM audit_logs WHERE session_id = :sid::uuid
                    ORDER BY created_at DESC LIMIT :limit OFFSET :offset
                    """
                ),
                {"sid": session_id, "limit": limit, "offset": offset},
            )
        ).mappings().all()
        return [dict(r) for r in rows], int(total)
