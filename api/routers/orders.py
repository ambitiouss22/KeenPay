"""Order routes."""

from fastapi import APIRouter, Depends, HTTPException

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from repositories.orders import OrderRepository
from schemas.order import OrderOut

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.get(
    "/{order_id}",
    response_model=OrderOut,
    dependencies=[Depends(require_perm(Permission.ORDER_READ_OWN))],
)
async def get_order(
    order_id: str,
    principal: CurrentUser,
):
    order = await OrderRepository().get(order_id)
    if not order:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "ORDER_NOT_FOUND", "message": "Not found"}}
        )
    return OrderOut(**order)
