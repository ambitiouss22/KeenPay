"""KeenPay background worker entrypoint."""

from jobs.hold_expiry import run_hold_expiry_loop
from jobs.reconciliation import run_reconciliation_loop
from webhook_processor import run_webhook_consumer


def main() -> None:
    # Run consumers in separate processes or asyncio tasks in production.
    run_webhook_consumer()
    run_hold_expiry_loop()
    run_reconciliation_loop()


if __name__ == "__main__":
    main()
