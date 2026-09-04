"""Retry provider events the request path recorded but could not finish.

The API route applies an event inline, which is what makes the response
truthful: the provider is told "processed" only once it was. This consumer
exists for the gap that leaves — an event claimed and stored, whose handler
then failed on something transient (the database blinked, a lookup timed out).
Those rows sit unprocessed, and nothing else would ever come back for them.

It is a sweeper, not the primary path, and that distinction matters: it never
re-verifies a signature (the row would not exist if verification had failed)
and it never re-applies an event already marked processed, because the claim
that made the row is also what makes reprocessing impossible.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger(__name__)


async def drain_once(*, limit: int = 100) -> int:
    """Retry every unprocessed event once. Returns how many were retried."""
    from modules.webhooks.processor import WebhookProcessor
    from repositories.webhooks import WebhookRepository

    events = WebhookRepository()
    processor = WebhookProcessor(events=events)

    pending = await events.list_unprocessed(limit=limit)
    for event in pending:
        try:
            outcome = await processor.reapply(event)
            await events.mark_processed(
                event["event_id"],
                result={"verdict": outcome.verdict.value, "message": outcome.message},
                order_id=outcome.order_id,
            )
            logger.info(
                "webhook_retry_processed",
                event_id=event["event_id"],
                verdict=outcome.verdict.value,
            )
        except Exception:  # noqa: BLE001 - one bad event must not stop the sweep
            logger.exception("webhook_retry_failed", event_id=event.get("event_id"))

    return len(pending)


async def run_webhook_consumer(
    *,
    interval_seconds: int | None = None,
    iterations: int | None = None,
) -> None:
    """Sweep unprocessed events on a fixed interval.

    ``iterations`` bounds the loop so a test can drive it without running
    forever; left unset the loop runs until the process is stopped.
    """
    from config.settings import get_settings

    interval = interval_seconds or get_settings().webhook_retry_interval_seconds

    completed = 0
    while iterations is None or completed < iterations:
        try:
            retried = await drain_once()
            if retried:
                logger.info("webhook_sweep", retried=retried)
        except Exception:  # noqa: BLE001 - the loop must outlive one bad sweep
            logger.exception("webhook_sweep_failed")

        completed += 1
        if iterations is not None and completed >= iterations:
            break
        await asyncio.sleep(interval)


def main() -> None:
    """Entry point for running the consumer as its own process."""
    asyncio.run(run_webhook_consumer())


if __name__ == "__main__":
    main()
