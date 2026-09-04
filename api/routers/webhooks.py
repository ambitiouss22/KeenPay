"""Razorpay webhook route.

Deliberately thin. Every rule about verifying, deduplicating and applying an
event lives in :mod:`modules.webhooks.processor`, so those rules can be tested
directly instead of through an HTTP client, and so this file cannot quietly
grow a second copy of them.

The route reads the raw body and hands it over untouched. Anything that
re-serializes it first breaks the signature, because the signature covers the
bytes that arrived, not their meaning.
"""

from fastapi import APIRouter, Header, HTTPException, Request

from modules.webhooks.processor import WebhookProcessor

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_webhook_processor() -> WebhookProcessor:
    return WebhookProcessor()


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
):
    """Ingest one provider event."""
    raw_body = await request.body()
    processor = get_webhook_processor()

    outcome = await processor.handle(
        raw_body,
        x_razorpay_signature or None,
        header_event_id=x_razorpay_event_id or None,
    )

    if not outcome.ok:
        raise HTTPException(status_code=outcome.status_code, detail=outcome.body())
    return outcome.body()
