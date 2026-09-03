"""Tenant-scoped repositories.

Every repository here takes a session that is already pinned to a tenant, and
stamps ``tenant_id`` on everything it writes. The filtering is defence in depth,
not the defence itself — row-level security in the database is what actually
makes cross-tenant access impossible. If a query here forgot its WHERE clause,
Postgres would still return only the pinned tenant's rows.

That ordering matters. Application-level filtering alone fails the moment
someone writes a new query and forgets; a database policy cannot be forgotten.

These live alongside the existing ``api/repositories/*.py``, which remain the
v1 write path. New code should prefer these.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.rls import assert_tenant_pinned


class InsufficientBudgetError(RuntimeError):
    """A campaign reservation would exceed the remaining budget."""


class InsufficientInventoryError(RuntimeError):
    """A stock reservation would exceed available quantity."""


@dataclass(frozen=True)
class Reservation:
    """Outcome of a successful atomic reservation."""

    subject_id: str
    reserved: int
    remaining: int


class TenantScopedRepository:
    """Base for repositories that operate inside one tenant.

    ``tenant_id`` is resolved lazily from the session rather than passed in, so
    a repository can never be constructed pointing at a tenant the session is
    not actually pinned to.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tenant_id: uuid.UUID | None = None

    async def tenant_id(self) -> uuid.UUID:
        """The tenant this repository is bound to.

        Raises ``TenantNotPinnedError`` if the session was never pinned, which
        turns an otherwise baffling empty result set into a precise error.
        """
        if self._tenant_id is None:
            self._tenant_id = await assert_tenant_pinned(self._session)
        return self._tenant_id

    async def _fetch_one(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        row = (await self._session.execute(text(sql), params)).mappings().first()
        return dict(row) if row else None

    async def _fetch_all(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = (await self._session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------


class OrderRepository(TenantScopedRepository):
    """Orders for the pinned tenant."""

    async def get(self, order_id: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM orders WHERE id = :id", {"id": order_id}
        )

    async def get_by_payment_link(self, payment_link_id: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM orders WHERE razorpay_payment_link_id = :pid",
            {"pid": payment_link_id},
        )

    async def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM orders WHERE idempotency_key = :key", {"key": key}
        )

    async def list_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._fetch_all(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT :limit",
            {"limit": limit},
        )

    async def create_pending(
        self,
        *,
        order_id: str,
        session_id: str | uuid.UUID,
        merchant_id: str,
        user_id: str | None,
        line_items: list[dict],
        subtotal_paise: int,
        discount_amount_paise: int,
        final_amount_paise: int,
        guardrail_decision_id: str | uuid.UUID,
        offer_version: int,
        policy_version: str,
        idempotency_key: str,
        razorpay_payment_link_id: str | None = None,
        razorpay_payment_link_url: str | None = None,
        payment_link_expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a pending order.

        ``tenant_id`` comes from the session, never from the caller — an order
        cannot be created against a tenant the request is not pinned to, and the
        RLS ``WITH CHECK`` clause would reject it even if this tried.
        """
        tenant_id = await self.tenant_id()
        return await self._fetch_one(
            """
            INSERT INTO orders (
                id, tenant_id, session_id, merchant_id, user_id, status,
                subtotal_paise, discount_amount_paise, final_amount_paise, currency,
                line_items, guardrail_decision_id, offer_version, policy_version,
                razorpay_payment_link_id, razorpay_payment_link_url,
                idempotency_key, payment_link_expires_at
            ) VALUES (
                :id, :tenant_id, :session_id, :merchant_id, :user_id, 'pending',
                :subtotal_paise, :discount_amount_paise, :final_amount_paise, 'INR',
                CAST(:line_items AS jsonb), :guardrail_decision_id, :offer_version,
                :policy_version, :link_id, :link_url, :idempotency_key, :expires_at
            )
            RETURNING *
            """,
            {
                "id": order_id,
                "tenant_id": str(tenant_id),
                "session_id": str(session_id),
                "merchant_id": merchant_id,
                "user_id": user_id,
                "subtotal_paise": subtotal_paise,
                "discount_amount_paise": discount_amount_paise,
                "final_amount_paise": final_amount_paise,
                "line_items": json.dumps(line_items),
                "guardrail_decision_id": str(guardrail_decision_id),
                "offer_version": offer_version,
                "policy_version": policy_version,
                "link_id": razorpay_payment_link_id,
                "link_url": razorpay_payment_link_url,
                "idempotency_key": idempotency_key,
                "expires_at": payment_link_expires_at,
            },
        )

    async def mark_paid(self, order_id: str, *, payment_id: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            """
            UPDATE orders
               SET status = 'paid', razorpay_payment_id = :pid, paid_at = NOW()
             WHERE id = :id AND status = 'pending'
            RETURNING *
            """,
            {"id": order_id, "pid": payment_id},
        )


# -----------------------------------------------------------------------------
# Payments
# -----------------------------------------------------------------------------


class PaymentRepository(TenantScopedRepository):
    """Payments and their attempts, for the pinned tenant."""

    async def get(self, payment_id: str | uuid.UUID) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM payments WHERE id = :id", {"id": str(payment_id)}
        )

    async def list_for_order(self, order_id: str) -> list[dict[str, Any]]:
        return await self._fetch_all(
            "SELECT * FROM payments WHERE order_id = :oid ORDER BY created_at",
            {"oid": order_id},
        )

    async def create(
        self,
        *,
        order_id: str,
        amount_paise: int,
        idempotency_key: str,
        provider: str = "razorpay",
        provider_order_id: str | None = None,
        status: str = "created",
    ) -> dict[str, Any]:
        tenant_id = await self.tenant_id()
        return await self._fetch_one(
            """
            INSERT INTO payments (
                tenant_id, order_id, status, amount_paise, currency,
                provider, provider_order_id, idempotency_key
            ) VALUES (
                :tenant_id, :order_id, CAST(:status AS payment_status),
                :amount_paise, 'INR', :provider, :provider_order_id, :idempotency_key
            )
            RETURNING *
            """,
            {
                "tenant_id": str(tenant_id),
                "order_id": order_id,
                "status": status,
                "amount_paise": amount_paise,
                "provider": provider,
                "provider_order_id": provider_order_id,
                "idempotency_key": idempotency_key,
            },
        )

    async def mark_captured(
        self, payment_id: str | uuid.UUID, *, provider_payment_id: str
    ) -> dict[str, Any] | None:
        return await self._fetch_one(
            """
            UPDATE payments
               SET status = 'captured', provider_payment_id = :ppid, captured_at = NOW()
             WHERE id = :id AND status IN ('created', 'authorized')
            RETURNING *
            """,
            {"id": str(payment_id), "ppid": provider_payment_id},
        )

    async def record_attempt(
        self,
        *,
        order_id: str,
        status: str,
        amount_paise: int,
        payment_id: str | uuid.UUID | None = None,
        attempt_number: int = 1,
        error_code: str | None = None,
        error_detail: dict | None = None,
    ) -> dict[str, Any]:
        """Record an attempt, successful or not.

        Failures are the point: ``payments`` keeps what settled, this keeps the
        full history including everything that did not.
        """
        tenant_id = await self.tenant_id()
        return await self._fetch_one(
            """
            INSERT INTO payment_attempts (
                tenant_id, order_id, payment_id, attempt_number,
                status, amount_paise, error_code, error_detail
            ) VALUES (
                :tenant_id, :order_id, :payment_id, :attempt_number,
                :status, :amount_paise, :error_code, CAST(:error_detail AS jsonb)
            )
            RETURNING *
            """,
            {
                "tenant_id": str(tenant_id),
                "order_id": order_id,
                "payment_id": str(payment_id) if payment_id else None,
                "attempt_number": attempt_number,
                "status": status,
                "amount_paise": amount_paise,
                "error_code": error_code,
                "error_detail": json.dumps(error_detail or {}),
            },
        )


# -----------------------------------------------------------------------------
# Campaigns
# -----------------------------------------------------------------------------


class CampaignRepository(TenantScopedRepository):
    """Discount campaigns and their budget."""

    async def get(self, campaign_id: str | uuid.UUID) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM campaigns WHERE id = :id", {"id": str(campaign_id)}
        )

    async def list_active(self) -> list[dict[str, Any]]:
        return await self._fetch_all(
            """
            SELECT * FROM campaigns
             WHERE active
               AND (starts_at IS NULL OR starts_at <= NOW())
               AND (ends_at   IS NULL OR ends_at   >= NOW())
             ORDER BY created_at DESC
            """,
            {},
        )

    async def create(
        self,
        *,
        name: str,
        budget_paise: int,
        code: str | None = None,
        max_discount_pct: float | None = None,
    ) -> dict[str, Any]:
        tenant_id = await self.tenant_id()
        return await self._fetch_one(
            """
            INSERT INTO campaigns (tenant_id, name, code, budget_paise, max_discount_pct)
            VALUES (:tenant_id, :name, :code, :budget_paise, :max_discount_pct)
            RETURNING *
            """,
            {
                "tenant_id": str(tenant_id),
                "name": name,
                "code": code,
                "budget_paise": budget_paise,
                "max_discount_pct": max_discount_pct,
            },
        )

    async def atomic_reserve(
        self,
        campaign_id: str | uuid.UUID,
        amount_paise: int,
        *,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> Reservation:
        """Reserve budget, or raise if it would overspend.

        Concurrency safety comes from doing the check and the write in a single
        UPDATE. Postgres takes a row lock for the update and re-evaluates the
        WHERE clause under that lock, so two callers racing for the last rupee
        cannot both succeed — the loser matches zero rows.

        The read-then-write shape people reach for first::

            row = SELECT ... FROM campaigns WHERE id = ...
            if row.remaining >= amount:          # <- both racers see the same
                UPDATE campaigns SET reserved = reserved + amount

        is exactly the shape that double-spends under load.

        ``campaigns_budget_not_exceeded`` backstops this at the schema level, so
        even a future caller who bypasses this method cannot overspend.
        """
        if amount_paise <= 0:
            raise ValueError("Reservation amount must be positive")

        row = await self._fetch_one(
            """
            UPDATE campaigns
               SET reserved_paise = reserved_paise + :amount
             WHERE id = :id
               AND active
               AND reserved_paise + spent_paise + :amount <= budget_paise
            RETURNING id,
                      reserved_paise,
                      budget_paise - reserved_paise - spent_paise AS remaining_paise
            """,
            {"id": str(campaign_id), "amount": amount_paise},
        )

        if row is None:
            # Distinguish "no such campaign" from "not enough budget"; the caller
            # handles them very differently.
            existing = await self.get(campaign_id)
            if existing is None:
                raise InsufficientBudgetError(f"No campaign {campaign_id} for this tenant")
            remaining = (
                existing["budget_paise"] - existing["reserved_paise"] - existing["spent_paise"]
            )
            raise InsufficientBudgetError(
                f"Campaign {campaign_id} has {remaining} paise remaining, "
                f"cannot reserve {amount_paise}"
            )

        await self._append_ledger(
            campaign_id=campaign_id,
            order_id=order_id,
            entry_type="reserve",
            amount_paise=amount_paise,
            balance_after_paise=row["remaining_paise"],
            reason=reason,
        )

        return Reservation(
            subject_id=str(row["id"]),
            reserved=amount_paise,
            remaining=row["remaining_paise"],
        )

    async def release(
        self,
        campaign_id: str | uuid.UUID,
        amount_paise: int,
        *,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> Reservation:
        """Return a reservation to the pool, e.g. after an abandoned checkout."""
        row = await self._fetch_one(
            """
            UPDATE campaigns
               SET reserved_paise = GREATEST(reserved_paise - :amount, 0)
             WHERE id = :id
            RETURNING id,
                      reserved_paise,
                      budget_paise - reserved_paise - spent_paise AS remaining_paise
            """,
            {"id": str(campaign_id), "amount": amount_paise},
        )
        if row is None:
            raise InsufficientBudgetError(f"No campaign {campaign_id} for this tenant")

        await self._append_ledger(
            campaign_id=campaign_id,
            order_id=order_id,
            entry_type="release",
            amount_paise=amount_paise,
            balance_after_paise=row["remaining_paise"],
            reason=reason,
        )
        return Reservation(
            subject_id=str(row["id"]), reserved=0, remaining=row["remaining_paise"]
        )

    async def commit_spend(
        self,
        campaign_id: str | uuid.UUID,
        amount_paise: int,
        *,
        order_id: str | None = None,
    ) -> Reservation:
        """Convert a reservation into actual spend once payment settles."""
        row = await self._fetch_one(
            """
            UPDATE campaigns
               SET reserved_paise = GREATEST(reserved_paise - :amount, 0),
                   spent_paise    = spent_paise + :amount
             WHERE id = :id
            RETURNING id,
                      spent_paise,
                      budget_paise - reserved_paise - spent_paise AS remaining_paise
            """,
            {"id": str(campaign_id), "amount": amount_paise},
        )
        if row is None:
            raise InsufficientBudgetError(f"No campaign {campaign_id} for this tenant")

        await self._append_ledger(
            campaign_id=campaign_id,
            order_id=order_id,
            entry_type="spend",
            amount_paise=amount_paise,
            balance_after_paise=row["remaining_paise"],
            reason="payment settled",
        )
        return Reservation(
            subject_id=str(row["id"]), reserved=0, remaining=row["remaining_paise"]
        )

    async def _append_ledger(
        self,
        *,
        campaign_id: str | uuid.UUID,
        order_id: str | None,
        entry_type: str,
        amount_paise: int,
        balance_after_paise: int | None,
        reason: str | None,
    ) -> None:
        """Write the ledger entry. Append-only, enforced by trigger."""
        tenant_id = await self.tenant_id()
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
                "tenant_id": str(tenant_id),
                "campaign_id": str(campaign_id),
                "order_id": order_id,
                "entry_type": entry_type,
                "amount_paise": amount_paise,
                "balance_after_paise": balance_after_paise,
                "reason": reason,
            },
        )


# -----------------------------------------------------------------------------
# Products
# -----------------------------------------------------------------------------


class ProductRepository(TenantScopedRepository):
    """Catalog for the pinned tenant."""

    async def get(self, product_id: str) -> dict[str, Any] | None:
        return await self._fetch_one(
            "SELECT * FROM products WHERE id = :id", {"id": product_id}
        )

    async def get_by_sku(self, sku: str) -> dict[str, Any] | None:
        return await self._fetch_one("SELECT * FROM products WHERE sku = :sku", {"sku": sku})

    async def list_active(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._fetch_all(
            """
            SELECT *, quantity_on_hand - quantity_reserved AS quantity_available
              FROM products
             WHERE active
             ORDER BY name
             LIMIT :limit
            """,
            {"limit": limit},
        )

    async def atomic_reserve(self, product_id: str, quantity: int) -> Reservation:
        """Reserve stock, or raise if not enough is available.

        Same single-statement pattern as campaign budget, for the same reason:
        the availability check happens under the row lock the UPDATE takes, so
        concurrent reservations cannot both pass a check that only one of them
        should.
        """
        if quantity <= 0:
            raise ValueError("Reservation quantity must be positive")

        row = await self._fetch_one(
            """
            UPDATE products
               SET quantity_reserved = quantity_reserved + :qty
             WHERE id = :id
               AND active
               AND quantity_on_hand - quantity_reserved >= :qty
            RETURNING id, quantity_reserved,
                      quantity_on_hand - quantity_reserved AS quantity_available
            """,
            {"id": product_id, "qty": quantity},
        )

        if row is None:
            existing = await self.get(product_id)
            if existing is None:
                raise InsufficientInventoryError(
                    f"No product {product_id} for this tenant"
                )
            available = existing["quantity_on_hand"] - existing["quantity_reserved"]
            raise InsufficientInventoryError(
                f"Product {product_id} has {available} available, cannot reserve {quantity}"
            )

        return Reservation(
            subject_id=str(row["id"]),
            reserved=quantity,
            remaining=row["quantity_available"],
        )

    async def release(self, product_id: str, quantity: int) -> Reservation:
        """Return reserved stock to the available pool."""
        row = await self._fetch_one(
            """
            UPDATE products
               SET quantity_reserved = GREATEST(quantity_reserved - :qty, 0)
             WHERE id = :id
            RETURNING id, quantity_on_hand - quantity_reserved AS quantity_available
            """,
            {"id": product_id, "qty": quantity},
        )
        if row is None:
            raise InsufficientInventoryError(f"No product {product_id} for this tenant")
        return Reservation(
            subject_id=str(row["id"]), reserved=0, remaining=row["quantity_available"]
        )


# -----------------------------------------------------------------------------
# Audit
# -----------------------------------------------------------------------------


class AuditRepository(TenantScopedRepository):
    """Append-only audit trail. UPDATE and DELETE are blocked by trigger."""

    async def append(
        self,
        *,
        actor: str,
        action: str,
        merchant_id: str,
        session_id: str | uuid.UUID | None = None,
        order_id: str | None = None,
        decision_id: str | uuid.UUID | None = None,
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        trace_metadata: dict | None = None,
    ) -> dict[str, Any]:
        tenant_id = await self.tenant_id()
        return await self._fetch_one(
            """
            INSERT INTO audit_logs (
                tenant_id, merchant_id, session_id, order_id,
                actor, action, decision_id,
                input_snapshot, output_snapshot, trace_metadata
            ) VALUES (
                :tenant_id, :merchant_id, :session_id, :order_id,
                CAST(:actor AS audit_actor), :action, :decision_id,
                CAST(:input_snapshot AS jsonb),
                CAST(:output_snapshot AS jsonb),
                CAST(:trace_metadata AS jsonb)
            )
            RETURNING *
            """,
            {
                "tenant_id": str(tenant_id),
                "merchant_id": merchant_id,
                "session_id": str(session_id) if session_id else None,
                "order_id": order_id,
                "actor": actor,
                "action": action,
                "decision_id": str(decision_id) if decision_id else None,
                "input_snapshot": json.dumps(input_snapshot or {}),
                "output_snapshot": json.dumps(output_snapshot or {}),
                "trace_metadata": json.dumps(trace_metadata or {}),
            },
        )

    async def list_for_session(self, session_id: str | uuid.UUID) -> list[dict[str, Any]]:
        return await self._fetch_all(
            "SELECT * FROM audit_logs WHERE session_id = :sid ORDER BY created_at",
            {"sid": str(session_id)},
        )


__all__ = [
    "AuditRepository",
    "CampaignRepository",
    "InsufficientBudgetError",
    "InsufficientInventoryError",
    "OrderRepository",
    "PaymentRepository",
    "ProductRepository",
    "Reservation",
    "TenantScopedRepository",
]
