"""Order persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

_MEMORY_ORDERS: dict[str, dict[str, Any]] = {}


class OrderRepository:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    async def create_pending(
        self,
        *,
        session_id: str,
        merchant_id: str,
        user_id: str | None,
        line_items: list[dict],
        subtotal_paise: int,
        discount_amount_paise: int,
        final_amount_paise: int,
        guardrail_decision_id: str,
        offer_version: int,
        policy_version: str,
        idempotency_key: str,
        razorpay_payment_link_id: str,
        razorpay_payment_link_url: str,
    ) -> dict[str, Any]:
        order_id = f"ord_{uuid4().hex[:12]}"
        record = {
            "id": order_id,
            "session_id": session_id,
            "merchant_id": merchant_id,
            "user_id": user_id,
            "status": "pending",
            "subtotal_paise": subtotal_paise,
            "discount_amount_paise": discount_amount_paise,
            "final_amount_paise": final_amount_paise,
            "currency": "INR",
            "line_items": line_items,
            "guardrail_decision_id": guardrail_decision_id,
            "offer_version": offer_version,
            "policy_version": policy_version,
            "razorpay_payment_link_id": razorpay_payment_link_id,
            "razorpay_payment_link_url": razorpay_payment_link_url,
            "razorpay_payment_id": None,
            "payment_link_expires_at": datetime.now(UTC) + timedelta(hours=24),
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "paid_at": None,
        }
        if self._memory:
            _MEMORY_ORDERS[order_id] = record
            return record

        assert self._session is not None
        sql = text(
            """
            INSERT INTO orders (
                id, session_id, merchant_id, user_id, status,
                subtotal_paise, discount_amount_paise, final_amount_paise, currency,
                line_items, guardrail_decision_id, offer_version, policy_version,
                razorpay_payment_link_id, razorpay_payment_link_url, idempotency_key,
                payment_link_expires_at
            ) VALUES (
                :id, :session_id::uuid, :merchant_id, :user_id, 'pending',
                :subtotal_paise, :discount_amount_paise, :final_amount_paise, 'INR',
                :line_items::jsonb, :guardrail_decision_id::uuid, :offer_version, :policy_version,
                :razorpay_payment_link_id, :razorpay_payment_link_url, :idempotency_key,
                :expires_at
            )
            """
        )
        await self._session.execute(
            sql,
            {
                **record,
                "line_items": json.dumps(line_items),
                "expires_at": record["payment_link_expires_at"],
            },
        )
        return record

    async def get(self, order_id: str) -> dict[str, Any] | None:
        if self._memory:
            return _MEMORY_ORDERS.get(order_id)
        assert self._session is not None
        row = (
            (
                await self._session.execute(
                    text("SELECT * FROM orders WHERE id = :id"), {"id": order_id}
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_by_payment_link(self, payment_link_id: str) -> dict[str, Any] | None:
        if self._memory:
            return next(
                (
                    o
                    for o in _MEMORY_ORDERS.values()
                    if o["razorpay_payment_link_id"] == payment_link_id
                ),
                None,
            )
        assert self._session is not None
        row = (
            (
                await self._session.execute(
                    text("SELECT * FROM orders WHERE razorpay_payment_link_id = :pid"),
                    {"pid": payment_link_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def mark_paid(self, order_id: str, *, payment_id: str) -> dict[str, Any] | None:
        if self._memory:
            order = _MEMORY_ORDERS.get(order_id)
            if order:
                order["status"] = "paid"
                order["razorpay_payment_id"] = payment_id
                order["paid_at"] = datetime.now(UTC)
            return order
        assert self._session is not None
        await self._session.execute(
            text(
                "UPDATE orders SET status = 'paid', razorpay_payment_id = :pid, "
                "paid_at = NOW() WHERE id = :id"
            ),
            {"id": order_id, "pid": payment_id},
        )
        return await self.get(order_id)
