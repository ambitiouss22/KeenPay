"""Carts and checkout.

Every route resolves the cart through :class:`PurchaseFlow`, which scopes it to
the caller's merchant and answers 404 for anything outside. No handler here
touches a repository directly - that is what keeps the ownership check from
being something each new route has to remember.

Checkout produces a pending order. It does not take money; the policy engine
and Razorpay do that in later phases, so a pricing bug and a payment bug cannot
be the same bug.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.commerce.flow import PurchaseFlow
from schemas.commerce import AddItemRequest, CartOut, CheckoutRequest, OrderOut

router = APIRouter(prefix="/api/v1/carts", tags=["carts"])


def get_purchase_flow() -> PurchaseFlow:
    return PurchaseFlow()


FlowDep = Annotated[PurchaseFlow, Depends(get_purchase_flow)]


@router.post(
    "",
    response_model=CartOut,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def create_cart(principal: CurrentUser, flow: FlowDep) -> CartOut:
    """Open a cart for the authenticated caller.

    Takes no body. Merchant and user come from the token; accepting them would
    let a caller open a cart belonging to someone else.
    """
    cart = await flow.create_cart(
        merchant_id=principal.merchant_id,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    return CartOut(**cart)


@router.get(
    "/{cart_id}",
    response_model=CartOut,
    dependencies=[Depends(require_perm(Permission.SESSION_READ_OWN))],
)
async def get_cart(cart_id: str, principal: CurrentUser, flow: FlowDep) -> CartOut:
    cart = await flow.get_cart(cart_id, merchant_id=principal.merchant_id)
    return CartOut(**cart)


@router.post(
    "/{cart_id}/items",
    response_model=CartOut,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def add_item(
    cart_id: str, body: AddItemRequest, principal: CurrentUser, flow: FlowDep
) -> CartOut:
    """Add a line. The price is read from the catalogue, never from the body."""
    cart = await flow.add_item(
        cart_id,
        merchant_id=principal.merchant_id,
        sku=body.sku,
        quantity=body.quantity,
    )
    return CartOut(**cart)


@router.delete(
    "/{cart_id}/items/{item_id}",
    response_model=CartOut,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def remove_item(
    cart_id: str, item_id: str, principal: CurrentUser, flow: FlowDep
) -> CartOut:
    cart = await flow.remove_item(cart_id, item_id, merchant_id=principal.merchant_id)
    return CartOut(**cart)


@router.post(
    "/{cart_id}/clear",
    response_model=CartOut,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def clear_cart(cart_id: str, principal: CurrentUser, flow: FlowDep) -> CartOut:
    cart = await flow.clear_cart(cart_id, merchant_id=principal.merchant_id)
    return CartOut(**cart)


@router.post(
    "/{cart_id}/checkout",
    response_model=OrderOut,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def checkout(
    cart_id: str, body: CheckoutRequest, principal: CurrentUser, flow: FlowDep
) -> OrderOut:
    """Convert the cart into a pending order.

    Totals are recomputed from the lines here; nothing the client sent about
    money is taken on trust. Calling this twice on one cart is a 409, not a
    second order.
    """
    order = await flow.checkout(
        cart_id,
        merchant_id=principal.merchant_id,
        user_id=principal.user_id,
        idempotency_key=body.idempotency_key,
        discount_paise=body.discount_paise,
    )
    return OrderOut(**order)
