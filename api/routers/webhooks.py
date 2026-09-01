"""Razorpay webhook handler."""

import json

from fastapi import APIRouter, Header, Request

from repositories.orders import OrderRepository
from repositories.webhooks import WebhookRepository
from services.audit import AuditService
from services.razorpay import RazorpayService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
):
    body = await request.body()
    rz = RazorpayService()
    if not rz.verify_webhook_signature(body, x_razorpay_signature):
        return {"status": "invalid_signature"}

    payload = json.loads(body)
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
