"""PurchaseFlow: cart to order.

The orchestrator that holds the sequence in one place. Routers call it; it
calls repositories and the safety checks. Keeping the ordering here rather than
spread across handlers is what makes "did we check stock before pricing?"
answerable by reading one function.

Every price is re-read from the catalogue at add time and every total is
recomputed at checkout. Nothing a client sends is trusted as a fact about
money - a client-supplied price or total is a request, and requests get
verified.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.logging import get_logger
from core.observability import record_event, span
from modules.catalog.service import CatalogService
from modules.commerce.safety import (
    assert_in_stock,
    cart_subtotal_paise,
    final_total_paise,
    validate_quantity,
)
from repositories.carts import CartRepository
from repositories.orders import OrderRepository

logger = get_logger(__name__)


class PurchaseFlow:
    """Cart lifecycle and the conversion to an order."""

    def __init__(
        self,
        *,
        catalog: CatalogService | None = None,
        carts: CartRepository | None = None,
        orders: OrderRepository | None = None,
    ) -> None:
        self._catalog = catalog or CatalogService()
        self._carts = carts or CartRepository()
        self._orders = orders or OrderRepository()

    # --- carts --------------------------------------------------------------

    async def create_cart(
        self, *, merchant_id: str, user_id: str | None, tenant_id: str | None = None
    ) -> dict[str, Any]:
        cart = await self._carts.create(
            merchant_id=merchant_id, user_id=user_id, tenant_id=tenant_id
        )
        record_event("cart_created")
        return self._with_totals(cart)

    async def get_cart(self, cart_id: str, *, merchant_id: str) -> dict[str, Any]:
        cart = await self._carts.get(cart_id, merchant_id=merchant_id)
        if cart is None:
            # Absent and belonging-to-another-merchant are the same answer, so
            # cart ids cannot be probed for existence across tenants.
            raise NotFoundError("CART_NOT_FOUND", f"No cart {cart_id!r}")
        return self._with_totals(cart)

    async def add_item(
        self, cart_id: str, *, merchant_id: str, sku: str, quantity: int
    ) -> dict[str, Any]:
        """Add a line, priced from the catalogue rather than from the request.

        The client names *what* and *how many*; the price is looked up here. A
        client that could name the price could name a lower one.
        """
        with span("cart.add_item", cart_id=cart_id, sku=sku):
            cart = await self.get_cart(cart_id, merchant_id=merchant_id)
            if cart["status"] != "open":
                raise ConflictError(
                    "CART_CLOSED", f"cart {cart_id} is {cart['status']}, not open"
                )

            qty = validate_quantity(quantity)
            product = await self._catalog.get_by_sku(merchant_id=merchant_id, sku=sku)
            if not product.get("active", True):
                raise ValidationError(
                    "PRODUCT_INACTIVE", f"{sku} is not available for sale", {"sku": sku}
                )

            # Stock is checked against the total the cart would hold, not the
            # increment: adding 3 twice to a stock of 5 must fail on the second
            # call, not succeed because 3 <= 5 both times.
            already = next((i["quantity"] for i in cart["items"] if i["sku"] == sku), 0)
            assert_in_stock(
                sku=sku,
                requested=already + qty,
                available=product.get("quantity_available", product["quantity_on_hand"]),
            )

            updated = await self._carts.add_item(
                cart_id,
                merchant_id=merchant_id,
                sku=sku,
                name=product["name"],
                unit_price_paise=product["list_price_paise"],
                quantity=qty,
            )
            if updated is None:  # pragma: no cover - get_cart already proved it exists
                raise NotFoundError("CART_NOT_FOUND", f"No cart {cart_id!r}")
            record_event("cart_item_added")
            return self._with_totals(updated)

    async def remove_item(
        self, cart_id: str, item_id: str, *, merchant_id: str
    ) -> dict[str, Any]:
        cart = await self.get_cart(cart_id, merchant_id=merchant_id)
        if cart["status"] != "open":
            raise ConflictError("CART_CLOSED", f"cart {cart_id} is {cart['status']}, not open")

        updated = await self._carts.remove_item(cart_id, item_id, merchant_id=merchant_id)
        if updated is None:
            raise NotFoundError("ITEM_NOT_FOUND", f"No item {item_id!r} in this cart")
        record_event("cart_item_removed")
        return self._with_totals(updated)

    async def clear_cart(self, cart_id: str, *, merchant_id: str) -> dict[str, Any]:
        await self.get_cart(cart_id, merchant_id=merchant_id)
        cleared = await self._carts.clear(cart_id, merchant_id=merchant_id)
        if cleared is None:  # pragma: no cover
            raise NotFoundError("CART_NOT_FOUND", f"No cart {cart_id!r}")
        return self._with_totals(cleared)

    # --- checkout -----------------------------------------------------------

    async def checkout(
        self,
        cart_id: str,
        *,
        merchant_id: str,
        user_id: str | None,
        idempotency_key: str,
        discount_paise: int = 0,
    ) -> dict[str, Any]:
        """Turn a cart into a pending order.

        Deliberately does *not* take payment. That is the policy engine's and
        Razorpay's job in later phases; this produces the order they act on, so
        the two concerns fail independently.

        Stock is re-checked here even though ``add_item`` checked it. Time
        passes between filling a cart and paying, and someone else may have
        bought the last one in between.
        """
        with span("cart.checkout", cart_id=cart_id):
            cart = await self.get_cart(cart_id, merchant_id=merchant_id)

            if cart["status"] != "open":
                raise ConflictError(
                    "CART_ALREADY_CHECKED_OUT",
                    f"cart {cart_id} is {cart['status']}",
                    {"cart_id": cart_id, "status": cart["status"]},
                )
            if not cart["items"]:
                raise ValidationError("CART_EMPTY", "cannot check out an empty cart")

            for item in cart["items"]:
                product = await self._catalog.get_by_sku(
                    merchant_id=merchant_id, sku=item["sku"]
                )
                assert_in_stock(
                    sku=item["sku"],
                    requested=item["quantity"],
                    available=product.get(
                        "quantity_available", product["quantity_on_hand"]
                    ),
                )

            subtotal = cart_subtotal_paise(cart["items"])
            total = final_total_paise(subtotal, discount_paise)

            order_id = f"ord_{uuid.uuid4().hex[:16]}"
            # Closing the cart first is what makes double checkout safe: the
            # second concurrent call finds it already closed and stops before
            # creating a second order.
            closed = await self._carts.mark_checked_out(
                cart_id, merchant_id=merchant_id, order_id=order_id
            )
            if closed is None:
                raise ConflictError(
                    "CART_ALREADY_CHECKED_OUT", f"cart {cart_id} was checked out concurrently"
                )

            order = {
                "id": order_id,
                "cart_id": cart_id,
                "merchant_id": merchant_id,
                "tenant_id": cart.get("tenant_id"),
                "user_id": user_id,
                "status": "pending",
                "currency": "INR",
                "line_items": cart["items"],
                "subtotal_paise": subtotal,
                "discount_amount_paise": discount_paise,
                "final_amount_paise": total,
                "idempotency_key": idempotency_key,
            }
            logger.info(
                "order_created",
                order_id=order_id,
                cart_id=cart_id,
                lines=len(cart["items"]),
                final_amount_paise=total,
            )
            record_event("order_created")
            return order

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _with_totals(cart: dict[str, Any]) -> dict[str, Any]:
        """Attach recomputed totals.

        Computed on read rather than stored, so a stored total can never drift
        from the lines it is supposed to summarise.
        """
        items = cart.get("items", [])
        subtotal = cart_subtotal_paise(items) if items else 0
        return {
            **cart,
            "subtotal_paise": subtotal,
            "item_count": sum(i["quantity"] for i in items),
            "line_count": len(items),
        }


__all__ = ["PurchaseFlow"]
