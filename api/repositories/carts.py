"""Cart persistence, scoped to one merchant.

Mirrors the storage strategy of the other repositories: an in-memory store for
development and tests, a real table behind the same interface in deployment.

Every read and write takes ``merchant_id`` and filters on it. That is defence in
depth rather than the defence itself - row-level security is what makes
cross-tenant access impossible once carts move to Postgres - but a repository
that never learned to filter would be the thing that quietly leaks the day it
is pointed at a database without a policy.

A cart stores the price of each item **as it was when added**. Reading today's
price at checkout would let a catalogue edit silently change what a shopper
agreed to pay between adding and paying.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

_CARTS: dict[str, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def reset_carts() -> None:
    """Drop every cart. For test isolation only."""
    _CARTS.clear()


class CartRepository:
    """Carts for one merchant."""

    async def create(
        self, *, merchant_id: str, user_id: str | None, tenant_id: str | None = None
    ) -> dict[str, Any]:
        cart_id = f"cart_{uuid.uuid4().hex[:16]}"
        cart = {
            "id": cart_id,
            "merchant_id": merchant_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": "open",
            "items": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        _CARTS[cart_id] = cart
        return dict(cart)

    async def get(self, cart_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Fetch a cart, but only within this merchant.

        The merchant filter lives here rather than in the caller so that a new
        route cannot forget it. A cart belonging to someone else is reported as
        absent, never as forbidden - a 403 would confirm the id is real.
        """
        cart = _CARTS.get(cart_id)
        if cart is None or cart["merchant_id"] != merchant_id:
            return None
        return dict(cart)

    async def list_for_user(
        self, *, merchant_id: str, user_id: str | None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return [
            dict(c)
            for c in _CARTS.values()
            if c["merchant_id"] == merchant_id and c["user_id"] == user_id
        ][:limit]

    async def add_item(
        self,
        cart_id: str,
        *,
        merchant_id: str,
        sku: str,
        name: str,
        unit_price_paise: int,
        quantity: int,
    ) -> dict[str, Any] | None:
        cart = _CARTS.get(cart_id)
        if cart is None or cart["merchant_id"] != merchant_id:
            return None

        # Adding a sku that is already present increases its quantity rather
        # than creating a second line. Two lines for one sku make totals
        # correct but stock checks and edits ambiguous.
        for item in cart["items"]:
            if item["sku"] == sku:
                item["quantity"] += quantity
                item["line_total_paise"] = item["unit_price_paise"] * item["quantity"]
                cart["updated_at"] = _now()
                return dict(cart)

        cart["items"].append(
            {
                "item_id": f"item_{uuid.uuid4().hex[:12]}",
                "sku": sku,
                "name": name,
                "unit_price_paise": unit_price_paise,
                "quantity": quantity,
                "line_total_paise": unit_price_paise * quantity,
            }
        )
        cart["updated_at"] = _now()
        return dict(cart)

    async def remove_item(
        self, cart_id: str, item_id: str, *, merchant_id: str
    ) -> dict[str, Any] | None:
        cart = _CARTS.get(cart_id)
        if cart is None or cart["merchant_id"] != merchant_id:
            return None
        before = len(cart["items"])
        cart["items"] = [i for i in cart["items"] if i["item_id"] != item_id]
        if len(cart["items"]) == before:
            return None  # nothing removed: the item id was not in this cart
        cart["updated_at"] = _now()
        return dict(cart)

    async def clear(self, cart_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        cart = _CARTS.get(cart_id)
        if cart is None or cart["merchant_id"] != merchant_id:
            return None
        cart["items"] = []
        cart["updated_at"] = _now()
        return dict(cart)

    async def mark_checked_out(
        self, cart_id: str, *, merchant_id: str, order_id: str
    ) -> dict[str, Any] | None:
        """Close a cart against an order.

        Returns ``None`` if the cart is already closed, which is what makes
        double checkout detectable: the second attempt finds nothing to close
        rather than creating a second order for the same goods.
        """
        cart = _CARTS.get(cart_id)
        if cart is None or cart["merchant_id"] != merchant_id:
            return None
        if cart["status"] != "open":
            return None
        cart["status"] = "checked_out"
        cart["order_id"] = order_id
        cart["updated_at"] = _now()
        return dict(cart)


__all__ = ["CartRepository", "reset_carts"]
