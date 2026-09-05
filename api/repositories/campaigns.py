"""Campaign and budget-ledger persistence.

Mirrors the storage strategy of the other repositories in this package: an
in-memory store for development and tests, the real tables behind the same
interface in deployment.

**Where the atomicity lives.** ``reserve`` is written, in both backends, so that
the availability check and the write happen with nothing in between. In Postgres
that is a single UPDATE whose WHERE clause is re-evaluated under the row lock the
UPDATE itself takes. In the in-memory store it is a synchronous function with no
``await`` in its body, which under a single-threaded event loop is the same
guarantee: no other task can run between the check and the mutation.

The shape both avoid is the obvious one::

    state = await repo.get(campaign_id)      # both racers read the same figure
    if state.remaining >= amount:
        await repo.increase_reserved(...)    # both racers write

That is the shape that double-spends, and it double-spends under plain asyncio
concurrency, not merely under threads - every ``await`` is a scheduling point.

**Scoping.** The in-memory store filters on ``merchant_id``, as every repository
in this package does. The ``campaigns`` table has no ``merchant_id`` column; it
is tenant-scoped, and row-level security is what confines a SQL read to one
tenant. Both backends therefore refuse another owner's campaign, but by different
mechanisms, and the SQL one is the stronger of the two because it cannot be
forgotten by a future query.

**The ledger is append-only.** Every movement writes a ``budget_ledger`` row
before the operation returns. Nothing here updates or deletes one; in Postgres a
trigger enforces that. The ledger is what makes an overspend provable after the
fact rather than merely unlikely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

#: Campaign rows, keyed by id. Module level so a write is visible to every
#: repository instance, which is what makes the dev store behave like a database
#: rather than like per-request state.
_CAMPAIGNS: dict[str, dict[str, Any]] = {}

#: Append-only. A list, never a dict: entries are never addressed by key, only
#: read in order, and a list has no ``update`` to reach for by accident.
_LEDGER: list[dict[str, Any]] = []

ENTRY_RESERVE = "reserve"
ENTRY_RELEASE = "release"
ENTRY_SPEND = "spend"

_SELECT_CAMPAIGN = (
    "SELECT *, budget_paise - reserved_paise - spent_paise AS remaining_paise "
    "FROM campaigns"
)
_LIST_CAMPAIGNS = f"{_SELECT_CAMPAIGN} ORDER BY created_at DESC, id DESC LIMIT :limit"
_LIST_ACTIVE_CAMPAIGNS = (
    f"{_SELECT_CAMPAIGN} WHERE active ORDER BY created_at DESC, id DESC LIMIT :limit"
)


def reset_campaigns() -> None:
    """Drop every campaign and ledger entry. For test isolation only."""
    _CAMPAIGNS.clear()
    _LEDGER.clear()


def _now() -> datetime:
    return datetime.now(UTC)


def _remaining(row: dict[str, Any]) -> int:
    return int(row["budget_paise"]) - int(row["reserved_paise"]) - int(row["spent_paise"])


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """A copy, with the derived figure attached.

    A copy because the stored dict is the record: handing it out would let a
    caller mutate the budget by assigning to a field.
    """
    return {**row, "remaining_paise": _remaining(row)}


class CampaignRepository:
    """Campaigns for one merchant, and the ledger that records every movement."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    # --- reads --------------------------------------------------------------

    async def get(self, campaign_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Fetch one campaign, but only within this owner.

        A campaign belonging to someone else is reported as absent, never as
        forbidden. A 403 on a real id confirms the id is real to anyone
        enumerating them.
        """
        if self._memory:
            row = _CAMPAIGNS.get(str(campaign_id))
            if row is None or row["merchant_id"] != merchant_id:
                return None
            return _public(row)

        assert self._session is not None
        result = (
            await self._session.execute(
                text(f"{_SELECT_CAMPAIGN} WHERE id = :id"), {"id": str(campaign_id)}
            )
        ).mappings().first()
        return dict(result) if result else None

    async def list_for_merchant(
        self, *, merchant_id: str, active_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        if self._memory:
            rows = [r for r in _CAMPAIGNS.values() if r["merchant_id"] == merchant_id]
            if active_only:
                rows = [r for r in rows if r["active"]]
            # Newest first, then by id, so the order is total rather than
            # dependent on insertion timing at equal timestamps.
            rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
            return [_public(r) for r in rows[:limit]]

        assert self._session is not None
        # Two whole statements rather than one with an interpolated fragment.
        # Nothing here is user-controlled either way, but a query built by
        # concatenation is a query someone can later be tempted to concatenate
        # a parameter into.
        sql = _LIST_ACTIVE_CAMPAIGNS if active_only else _LIST_CAMPAIGNS
        rows = (
            await self._session.execute(text(sql), {"limit": limit})
        ).mappings().all()
        return [dict(r) for r in rows]

    async def ledger_for(
        self, campaign_id: str, *, merchant_id: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Every movement recorded against one campaign, oldest first.

        Not exposed over HTTP. It exists so that a test can assert against what
        actually happened to the budget rather than against what the API said
        happened - the two are only the same when the code is correct, which is
        the thing under test.
        """
        if self._memory:
            if await self.get(campaign_id, merchant_id=merchant_id) is None:
                return []
            return [dict(e) for e in _LEDGER if e["campaign_id"] == str(campaign_id)][:limit]

        assert self._session is not None
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM budget_ledger WHERE campaign_id = :id "
                    "ORDER BY created_at, id LIMIT :limit"
                ),
                {"id": str(campaign_id), "limit": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    # --- writes -------------------------------------------------------------

    async def create(
        self,
        *,
        merchant_id: str,
        name: str,
        budget_paise: int,
        code: str | None = None,
        max_discount_pct: Decimal | None = None,
        tenant_id: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "id": campaign_id,
            "merchant_id": merchant_id,
            "tenant_id": tenant_id,
            "name": name,
            "code": code,
            "budget_paise": budget_paise,
            "reserved_paise": 0,
            "spent_paise": 0,
            "max_discount_pct": max_discount_pct,
            "active": True,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "created_at": _now(),
            "updated_at": _now(),
        }

        if self._memory:
            _CAMPAIGNS[campaign_id] = record
            return _public(record)

        assert self._session is not None
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO campaigns (
                        id, tenant_id, name, code, budget_paise,
                        max_discount_pct, starts_at, ends_at
                    ) VALUES (
                        :id, :tenant_id, :name, :code, :budget_paise,
                        :max_discount_pct, :starts_at, :ends_at
                    )
                    RETURNING *, budget_paise - reserved_paise - spent_paise AS remaining_paise
                    """
                ),
                {
                    "id": campaign_id,
                    "tenant_id": tenant_id,
                    "name": name,
                    "code": code,
                    "budget_paise": budget_paise,
                    "max_discount_pct": max_discount_pct,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                },
            )
        ).mappings().first()
        return dict(row) if row else _public(record)

    async def reserve(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Move ``amount_paise`` from available to reserved, or refuse.

        Returns the updated campaign, or ``None`` when the reservation did not
        fit, the campaign is not active, or it does not belong to this owner.
        The caller distinguishes those cases by re-reading; conflating them here
        would mean either leaking the existence of another owner's campaign or
        losing the reason for a legitimate refusal.

        This is the method the hard cap rests on. Read the class docstring for
        why it is shaped the way it is.
        """
        if self._memory:
            return self._reserve_in_memory(
                str(campaign_id),
                merchant_id=merchant_id,
                amount_paise=amount_paise,
                order_id=order_id,
                reason=reason,
            )

        assert self._session is not None
        row = (
            await self._session.execute(
                text(
                    """
                    UPDATE campaigns
                       SET reserved_paise = reserved_paise + :amount,
                           updated_at     = NOW()
                     WHERE id = :id
                       AND active
                       AND reserved_paise + spent_paise + :amount <= budget_paise
                    RETURNING *,
                              budget_paise - reserved_paise - spent_paise AS remaining_paise
                    """
                ),
                {"id": str(campaign_id), "amount": amount_paise},
            )
        ).mappings().first()
        if row is None:
            return None

        updated = dict(row)
        await self._append_ledger_sql(
            campaign_id=str(campaign_id),
            tenant_id=str(updated.get("tenant_id")) if updated.get("tenant_id") else None,
            order_id=order_id,
            entry_type=ENTRY_RESERVE,
            amount_paise=amount_paise,
            balance_after_paise=int(updated["remaining_paise"]),
            reason=reason,
        )
        return updated

    async def release(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a reservation to the pool, e.g. after an abandoned checkout.

        Refuses to release more than is currently reserved. Without that clause
        a release would floor ``reserved`` at zero and the excess would reappear
        as headroom nobody funded - budget created by arithmetic.
        """
        if self._memory:
            return self._move_in_memory(
                str(campaign_id),
                merchant_id=merchant_id,
                amount_paise=amount_paise,
                entry_type=ENTRY_RELEASE,
                order_id=order_id,
                reason=reason,
            )

        assert self._session is not None
        row = (
            await self._session.execute(
                text(
                    """
                    UPDATE campaigns
                       SET reserved_paise = reserved_paise - :amount,
                           updated_at     = NOW()
                     WHERE id = :id
                       AND reserved_paise >= :amount
                    RETURNING *,
                              budget_paise - reserved_paise - spent_paise AS remaining_paise
                    """
                ),
                {"id": str(campaign_id), "amount": amount_paise},
            )
        ).mappings().first()
        if row is None:
            return None

        updated = dict(row)
        await self._append_ledger_sql(
            campaign_id=str(campaign_id),
            tenant_id=str(updated.get("tenant_id")) if updated.get("tenant_id") else None,
            order_id=order_id,
            entry_type=ENTRY_RELEASE,
            amount_paise=amount_paise,
            balance_after_paise=int(updated["remaining_paise"]),
            reason=reason,
        )
        return updated

    async def settle(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Convert a reservation into spend once the order is paid.

        ``remaining`` does not change: the money was already committed. What
        changes is that it stops being releasable, which is the whole difference
        between the two counters.
        """
        if self._memory:
            return self._move_in_memory(
                str(campaign_id),
                merchant_id=merchant_id,
                amount_paise=amount_paise,
                entry_type=ENTRY_SPEND,
                order_id=order_id,
                reason=reason,
            )

        assert self._session is not None
        row = (
            await self._session.execute(
                text(
                    """
                    UPDATE campaigns
                       SET reserved_paise = reserved_paise - :amount,
                           spent_paise    = spent_paise + :amount,
                           updated_at     = NOW()
                     WHERE id = :id
                       AND reserved_paise >= :amount
                    RETURNING *,
                              budget_paise - reserved_paise - spent_paise AS remaining_paise
                    """
                ),
                {"id": str(campaign_id), "amount": amount_paise},
            )
        ).mappings().first()
        if row is None:
            return None

        updated = dict(row)
        await self._append_ledger_sql(
            campaign_id=str(campaign_id),
            tenant_id=str(updated.get("tenant_id")) if updated.get("tenant_id") else None,
            order_id=order_id,
            entry_type=ENTRY_SPEND,
            amount_paise=amount_paise,
            balance_after_paise=int(updated["remaining_paise"]),
            reason=reason,
        )
        return updated

    # --- in-memory critical sections ----------------------------------------
    #
    # Both are plain ``def``. That is not an oversight and not a style choice:
    # an ``await`` anywhere between reading the counters and writing them back
    # would let another task observe the pre-write state and reserve the same
    # money twice. Keeping them synchronous is what makes the in-memory backend
    # honour the same guarantee the database gives.

    def _reserve_in_memory(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None,
        reason: str | None,
    ) -> dict[str, Any] | None:
        row = _CAMPAIGNS.get(campaign_id)
        if row is None or row["merchant_id"] != merchant_id or not row["active"]:
            return None
        if int(row["reserved_paise"]) + int(row["spent_paise"]) + amount_paise > int(
            row["budget_paise"]
        ):
            return None

        row["reserved_paise"] = int(row["reserved_paise"]) + amount_paise
        row["updated_at"] = _now()
        self._append_ledger_memory(
            row,
            entry_type=ENTRY_RESERVE,
            amount_paise=amount_paise,
            order_id=order_id,
            reason=reason,
        )
        return _public(row)

    def _move_in_memory(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        entry_type: str,
        order_id: str | None,
        reason: str | None,
    ) -> dict[str, Any] | None:
        row = _CAMPAIGNS.get(campaign_id)
        if row is None or row["merchant_id"] != merchant_id:
            return None
        if int(row["reserved_paise"]) < amount_paise:
            return None

        row["reserved_paise"] = int(row["reserved_paise"]) - amount_paise
        if entry_type == ENTRY_SPEND:
            row["spent_paise"] = int(row["spent_paise"]) + amount_paise
        row["updated_at"] = _now()
        self._append_ledger_memory(
            row,
            entry_type=entry_type,
            amount_paise=amount_paise,
            order_id=order_id,
            reason=reason,
        )
        return _public(row)

    @staticmethod
    def _append_ledger_memory(
        row: dict[str, Any],
        *,
        entry_type: str,
        amount_paise: int,
        order_id: str | None,
        reason: str | None,
    ) -> None:
        _LEDGER.append(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": row.get("tenant_id"),
                "campaign_id": row["id"],
                "order_id": order_id,
                "entry_type": entry_type,
                "amount_paise": amount_paise,
                "balance_after_paise": _remaining(row),
                "reason": reason,
                "created_at": _now(),
            }
        )

    async def _append_ledger_sql(
        self,
        *,
        campaign_id: str,
        tenant_id: str | None,
        order_id: str | None,
        entry_type: str,
        amount_paise: int,
        balance_after_paise: int,
        reason: str | None,
    ) -> None:
        assert self._session is not None
        await self._session.execute(
            text(
                """
                INSERT INTO budget_ledger (
                    tenant_id, campaign_id, order_id, entry_type,
                    amount_paise, balance_after_paise, reason
                ) VALUES (
                    :tenant_id, :campaign_id, :order_id, :entry_type,
                    :amount_paise, :balance_after_paise, :reason
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "order_id": order_id,
                "entry_type": entry_type,
                "amount_paise": amount_paise,
                "balance_after_paise": balance_after_paise,
                "reason": reason,
            },
        )


__all__ = [
    "ENTRY_RELEASE",
    "ENTRY_RESERVE",
    "ENTRY_SPEND",
    "CampaignRepository",
    "reset_campaigns",
]
