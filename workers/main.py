"""Background worker entrypoint.

The loops run concurrently as asyncio tasks in one process rather than
sequentially. Called one after another they would never both run: the first
loop never returns, so the second would never start — a bug that looks like
"reconciliation just never happens" and is invisible from the outside.

``return_exceptions=True`` keeps a crash in one loop from cancelling the
others. Each loop already survives its own bad iteration; this is the outer
belt for the case where one dies anyway.
"""

from __future__ import annotations

import asyncio

import structlog

from jobs.hold_expiry import run_hold_expiry_loop
from jobs.reconciliation import run_reconciliation_loop
from webhook_processor import run_webhook_consumer

logger = structlog.get_logger(__name__)


async def run_all() -> None:
    """Run every worker loop until the process is stopped."""
    results = await asyncio.gather(
        run_webhook_consumer(),
        run_reconciliation_loop(),
        _run_hold_expiry(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.error("worker_loop_exited", error=str(result))


async def _run_hold_expiry() -> None:
    """Adapt the hold-expiry loop, which is still synchronous."""
    outcome = run_hold_expiry_loop()
    if asyncio.iscoroutine(outcome):
        await outcome


def main() -> None:
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
