"""Negotiation session persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

_MEMORY_SESSIONS: dict[str, dict[str, Any]] = {}


class SessionRepository:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    async def create(
        self,
        *,
        merchant_id: str,
        user_id: str | None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid4())
        thread_id = session_id
        record = {
            "id": session_id,
            "merchant_id": merchant_id,
            "user_id": user_id,
            "status": "active",
            "negotiation_round": 0,
            "offer_version": 0,
            "parsed_intent": None,
            "search_results": [],
            "selected_line_items": [],
            "proposed_offer": None,
            "approved_offer": None,
            "guardrail_decision": None,
            "guardrail_decision_id": None,
            "guardrail_detail": None,
            "rejection_reasons": [],
            "user_confirmed_payment": False,
            "user_confirmed_at": None,
            "final_amount_paise": None,
            "currency": "INR",
            "anomaly_flags": [],
            "security_block": False,
            "langgraph_thread_id": thread_id,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "closed_at": None,
        }
        if self._memory:
            _MEMORY_SESSIONS[session_id] = record
            return record

        assert self._session is not None
        sql = text(
            """
            INSERT INTO negotiation_sessions (
                id, merchant_id, user_id, status, langgraph_thread_id, metadata
            ) VALUES (
                :id::uuid, :merchant_id, :user_id, 'active', :thread_id::uuid, :metadata::jsonb
            ) RETURNING id::text, status, created_at
            """
        )
        await self._session.execute(
            sql,
            {
                "id": session_id,
                "merchant_id": merchant_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "metadata": json.dumps(metadata or {}),
            },
        )
        return record

    async def get(self, session_id: str) -> dict[str, Any] | None:
        if self._memory:
            return _MEMORY_SESSIONS.get(session_id)

        assert self._session is not None
        sql = text("SELECT * FROM negotiation_sessions WHERE id = :id::uuid")
        row = (await self._session.execute(sql, {"id": session_id})).mappings().first()
        return dict(row) if row else None

    async def update(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        if self._memory:
            record = _MEMORY_SESSIONS.get(session_id)
            if not record:
                return None
            for key, value in fields.items():
                if key in ("proposed_offer", "approved_offer", "guardrail_detail", "parsed_intent"):
                    record[key] = value
                else:
                    record[key] = value
            record["updated_at"] = datetime.now(UTC)
            return record

        assert self._session is not None
        # Simplified: memory mode is primary for MVP orchestrator updates
        return await self.get(session_id)
