"""Opportunity persistence.

Same two-backend strategy as the rest of this package: an in-memory store for
development and tests, the ``opportunities`` table behind the same interface in
deployment.

**Writes are idempotent by construction.** The caller supplies the row id, and it
is derived from the suggestion's identity rather than allocated randomly - see
``modules.opportunities.service``. Storing is therefore an insert that does
nothing when the row already exists, in both backends. Running generation twice
produces the same rows, not two sets of near-duplicates a merchant has to
reconcile by eye.

**First write wins, deliberately.** A re-run does not overwrite an existing row.
``created_at`` stays at the moment the suggestion was first made, and - more
importantly - ``acted_on`` survives. Overwriting would silently resurrect
suggestions a merchant had already dealt with, every time generation ran.

The table is tenant-scoped and carries no ``merchant_id`` column, so the
in-memory store filters on ``merchant_id`` while the SQL store relies on
row-level security. The owning merchant is also recorded inside ``payload`` so a
row remains self-describing when read outside a pinned session.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

_OPPORTUNITIES: dict[str, dict[str, Any]] = {}


def reset_opportunities() -> None:
    """Drop every opportunity. For test isolation only."""
    _OPPORTUNITIES.clear()


def _now() -> datetime:
    return datetime.now(UTC)


class OpportunityRepository:
    """Growth suggestions for one merchant."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    async def insert_missing(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store any record whose id is not already present, and return them all.

        Returns the *stored* rows, existing ones included, in the order given.
        The API answers with what is in the store rather than with what was just
        computed, so a caller can never see a suggestion that was not persisted -
        and a second call to generate returns byte-identical bodies.
        """
        if not records:
            return []

        if self._memory:
            stored: list[dict[str, Any]] = []
            for record in records:
                existing = _OPPORTUNITIES.get(record["id"])
                if existing is None:
                    _OPPORTUNITIES[record["id"]] = {**record, "created_at": _now()}
                    existing = _OPPORTUNITIES[record["id"]]
                stored.append(dict(existing))
            return stored

        assert self._session is not None
        for record in records:
            await self._session.execute(
                text(
                    """
                    INSERT INTO opportunities (
                        id, tenant_id, session_id, user_id, kind, score, payload
                    ) VALUES (
                        :id, :tenant_id, :session_id, :user_id, :kind, :score,
                        CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": record["id"],
                    "tenant_id": record.get("tenant_id"),
                    "session_id": record.get("session_id"),
                    "user_id": record.get("user_id"),
                    "kind": record["kind"],
                    "score": record.get("score"),
                    "payload": json.dumps(record.get("payload") or {}),
                },
            )

        rows = (
            await self._session.execute(
                text("SELECT * FROM opportunities WHERE id = ANY(:ids)"),
                {"ids": [r["id"] for r in records]},
            )
        ).mappings().all()
        by_id = {str(r["id"]): dict(r) for r in rows}
        return [by_id[r["id"]] for r in records if r["id"] in by_id]

    async def list_for_merchant(
        self,
        *,
        merchant_id: str,
        kind: str | None = None,
        acted_on: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._memory:
            rows = [
                r
                for r in _OPPORTUNITIES.values()
                if (r.get("payload") or {}).get("merchant_id") == merchant_id
            ]
            if kind is not None:
                rows = [r for r in rows if r["kind"] == kind]
            if acted_on is not None:
                rows = [r for r in rows if bool(r.get("acted_on")) is acted_on]
            # Highest score first, then id. Both keys are needed: score alone
            # leaves ties resolved by dict order, which is insertion order, which
            # is not a property a caller should be able to depend on.
            rows.sort(key=lambda r: (-float(r.get("score") or 0), r["id"]))
            total = len(rows)
            return [dict(r) for r in rows[offset : offset + limit]], total

        assert self._session is not None
        clauses = ["payload ->> 'merchant_id' = :merchant_id"]
        params: dict[str, Any] = {
            "merchant_id": merchant_id,
            "limit": limit,
            "offset": offset,
        }
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if acted_on is not None:
            clauses.append("acted_on = :acted_on")
            params["acted_on"] = acted_on
        # `where` is built from the hardcoded clause list above; every
        # caller-supplied value travels as a bound parameter.
        where = " AND ".join(clauses)

        total = (
            await self._session.execute(
                text(f"SELECT COUNT(*) FROM opportunities WHERE {where}"),  # noqa: S608  # nosec B608
                params,
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    f"SELECT * FROM opportunities WHERE {where} "  # noqa: S608  # nosec B608
                    "ORDER BY score DESC NULLS LAST, id LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows], int(total)


__all__ = ["OpportunityRepository", "reset_opportunities"]
