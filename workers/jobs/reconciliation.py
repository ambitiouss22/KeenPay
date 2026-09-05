"""Periodically resolve payments the provider never answered about.

The loop is intentionally dull. All the judgement lives in
``modules.reconciliation.worker``; this file decides only *when* to ask, and
makes sure a failure in one pass does not end the process — a reconciliation
worker that dies on its first bad response is worse than none at all, because
everyone carries on assuming it is running.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger(__name__)


async def reconcile_once(merchant_id: str) -> dict:
    """Run one pass for one merchant and return its report."""
    from modules.reconciliation.worker import ReconciliationEngine

    report = await ReconciliationEngine().run(merchant_id, trigger="scheduled")
    return report.to_dict()


async def run_reconciliation_loop(
    merchant_ids: list[str] | None = None,
    *,
    interval_seconds: int | None = None,
    iterations: int | None = None,
) -> None:
    """Reconcile every merchant on a fixed interval.

    ``iterations`` bounds the loop so a test can drive it without running
    forever; left unset the loop runs until the process is stopped.
    """
    from config.settings import get_settings

    settings = get_settings()
    interval = interval_seconds or settings.reconciliation_interval_seconds
    targets = merchant_ids or ["merchant_keen"]

    completed = 0
    while iterations is None or completed < iterations:
        for merchant_id in targets:
            try:
                report = await reconcile_once(merchant_id)
            except Exception:  # noqa: BLE001 - the loop must outlive one bad pass
                logger.exception("reconciliation_pass_failed", merchant_id=merchant_id)
                continue
            # Named fields, not a splat: structlog reserves "event" for the
            # log message itself, so splatting a dict that grows such a key
            # raises from inside the logging call.
            logger.info(
                "reconciliation_pass",
                run_id=report["run_id"],
                merchant_id=report["merchant_id"],
                checked=report["checked"],
                resolved=report["resolved"],
                still_unknown=report["still_unknown"],
                diffs=len(report["diffs"]),
            )

        completed += 1
        if iterations is not None and completed >= iterations:
            break
        await asyncio.sleep(interval)


def main() -> None:
    """Entry point for running the loop as its own process."""
    asyncio.run(run_reconciliation_loop())


if __name__ == "__main__":
    main()
