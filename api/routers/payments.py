"""Payment routes.

There is no amount field on the create request, and that is the point: the
amount comes from the order. Roles are checked with the same dependency every
other router uses, so the payment path cannot drift from the rest of the API's
access rules.
"""

from fastapi import APIRouter, Depends, HTTPException

from dependencies.auth import CurrentUser, require_roles
from schemas.payments import (
    PaymentCreateRequest,
    PaymentOut,
    PaymentStatusOut,
    RefundCreateRequest,
)
from services.payments import PaymentService

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

#: Who may move money. A shopper's payment goes through the session flow, not
#: this route; a support agent may look but never charge or refund.
CAN_PAY = ("manager", "admin", "service")
CAN_READ = ("manager", "admin", "service", "support_agent")
CAN_REFUND = ("manager", "admin")


def get_payment_service() -> PaymentService:
    return PaymentService()


@router.post(
    "",
    response_model=PaymentOut,
    status_code=201,
    dependencies=[Depends(require_roles(*CAN_PAY))],
)
async def create_payment(
    body: PaymentCreateRequest,
    principal: CurrentUser,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentOut:
    """Charge for an order. The amount is read from the order, never the body."""
    result = await service.create_payment(
        merchant_id=principal.merchant_id,
        order_id=body.order_id,
        authorization_id=body.authorization_id,
        idempotency_key=body.idempotency_key,
        request_body=body.model_dump(),
    )
    if result["status_code"] != 201:
        raise HTTPException(status_code=result["status_code"], detail=result["body"])
    return PaymentOut(**result["body"])


@router.get(
    "/{payment_id}",
    response_model=PaymentOut,
    dependencies=[Depends(require_roles(*CAN_READ))],
)
async def get_payment(
    payment_id: str,
    principal: CurrentUser,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentOut:
    """Read one payment, scoped to the caller's merchant."""
    payment = await service.get_payment(payment_id, merchant_id=principal.merchant_id)
    if not payment:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PAYMENT_NOT_FOUND", "message": "Not found"}},
        )
    return PaymentOut(**payment)


@router.get(
    "/{payment_id}/status",
    response_model=PaymentStatusOut,
    dependencies=[Depends(require_roles(*CAN_READ))],
)
async def get_payment_status(
    payment_id: str,
    principal: CurrentUser,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentStatusOut:
    """Report status, reconciling an UNKNOWN payment against the provider."""
    payment_status = await service.get_status(payment_id, merchant_id=principal.merchant_id)
    if not payment_status:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PAYMENT_NOT_FOUND", "message": "Not found"}},
        )
    return PaymentStatusOut(**payment_status)


@router.post(
    "/{payment_id}/refund",
    response_model=PaymentOut,
    dependencies=[Depends(require_roles(*CAN_REFUND))],
)
async def refund_payment(
    payment_id: str,
    body: RefundCreateRequest,
    principal: CurrentUser,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentOut:
    """Send money back, never more than was taken."""
    result = await service.refund_payment(
        merchant_id=principal.merchant_id,
        payment_id=payment_id,
        authorization_id=body.authorization_id,
        amount_paise=body.amount_paise,
        idempotency_key=body.idempotency_key,
    )
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["body"])
    return PaymentOut(**result["body"])
