"""Razorpay webhook handler."""

import json

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from repositories.orders import OrderRepository
from repositories.webhooks import WebhookRepository
from services.audit import AuditService
from services.razorpay import RazorpayService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
):
    body = await request.body()
    rz = RazorpayService()

    if not rz.verify_webhook_signature(body, x_razorpay_signature):
        # 401, not 200. A 2xx tells Razorpay "delivered, do not retry", so a
        # merely misconfigured webhook secret would silently discard every
        # payment notification and orders would never be marked paid - a
        # failure that looks like success. A 401 makes them retry and shows up
        # in their dashboard.
        logger.warning("webhook_bad_signature", has_signature=bool(x_razorpay_signature))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_SIGNATURE", "message": "Bad webhook signature"}},
        )

    # This endpoint is public by necessity, so anything can be posted to it.
    # Unparseable or non-object bodies must be a 400, not an unhandled
    # exception - otherwise anyone can fill the logs with tracebacks.
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("webhook_unparseable_body", bytes=len(body))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_PAYLOAD", "message": "Body is not valid JSON"}},
        ) from exc

    if not isinstance(payload, dict):
        logger.warning("webhook_non_object_body", kind=type(payload).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_PAYLOAD", "message": "Body must be a JSON object"}},
        )

    event_id = payload.get("event_id") or payload.get("id", "")
    wh_repo = WebhookRepository()
    if not await wh_repo.register_event(event_id):
        return {"status": "duplicate"}

    event = payload.get("event", "")
    if event == "payment_link.paid":
        entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        link_id = entity.get("id")
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        payment_id = payment_entity.get("id", "pay_unknown")
        amount = payment_entity.get("amount", 0)

        order_repo = OrderRepository()
        audit = AuditService()
        order = await order_repo.get_by_payment_link(link_id)
        if order:
            if amount != order["final_amount_paise"]:
                order["status"] = "payment_disputed"
                await audit._repo.append(
                    session_id=order["session_id"],
                    order_id=order["id"],
                    merchant_id=order["merchant_id"],
                    actor="webhook",
                    action="PAYMENT_DISPUTED",
                    output_snapshot={"expected": order["final_amount_paise"], "received": amount},
                )
            else:
                await order_repo.mark_paid(order["id"], payment_id=payment_id)
                await audit._repo.append(
                    session_id=order["session_id"],
                    order_id=order["id"],
                    merchant_id=order["merchant_id"],
                    actor="webhook",
                    action="PAYMENT_CAPTURED",
                    output_snapshot={"payment_id": payment_id},
                )

    return {"status": "ok"}
