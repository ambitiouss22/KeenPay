"""Domain exceptions mapped to HTTP responses."""

from typing import Any


class KeenPayError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class PaymentGateError(KeenPayError):
    """Raised when assert_payment_gates() fails before Razorpay call."""
