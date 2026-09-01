"""Async idempotent Razorpay webhook processing.

ARCHITECTURE.md: workers/webhook_processor.py
Fast path: API verifies signature + inserts webhook_events.
Worker: match order, update status, publish trace, audit.
"""

def run_webhook_consumer() -> None:
    """Poll or subscribe to webhook_events queue and process idempotently."""
    raise NotImplementedError
