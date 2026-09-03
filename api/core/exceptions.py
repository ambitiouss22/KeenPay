"""Domain exceptions and the HTTP status each one maps to.

The status belongs on the exception, not in the handler. The previous version
decided in ``main.py`` with ``403 if code == "FORBIDDEN" else 400``, which meant
every new error type silently became a 400 - a "not found" answered 400, a
conflict answered 400. Putting ``status_code`` on the class makes the mapping
total and keeps it next to the definition.

Every error leaves the API in one shape::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

One envelope, always. A client that can parse a 400 can parse a 500.

``KeenPayError(code, message)`` positionally still works, so existing raise
sites are unaffected.
"""

from __future__ import annotations

from typing import Any


class KeenPayError(Exception):
    """Base for every expected domain failure.

    "Expected" is the distinction that matters. These carry a code and a
    message written to be shown to a caller. Anything that is *not* one of
    these is a bug, and the generic handler answers it with an opaque 500 -
    an unexpected exception's message may contain internals a caller must not
    see.
    """

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or type(self).code
        self.message = message or self.code.replace("_", " ").lower()
        self.details = details
        super().__init__(self.message)

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        if request_id:
            error["request_id"] = request_id
        return {"error": error}


class ValidationError(KeenPayError):
    status_code = 422
    code = "VALIDATION_ERROR"


class AuthenticationError(KeenPayError):
    """Who are you? Credentials absent, malformed, or expired."""

    status_code = 401
    code = "UNAUTHORIZED"


class AuthorizationError(KeenPayError):
    """Known caller, not allowed to do this."""

    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(KeenPayError):
    """Absent, or present but not visible to this caller.

    Deliberately the same answer for both. A 403 on a record that exists tells
    an attacker enumerating ids that it exists.
    """

    status_code = 404
    code = "NOT_FOUND"


class ConflictError(KeenPayError):
    """The request contradicts current state - double checkout, stale version."""

    status_code = 409
    code = "CONFLICT"


class RateLimitError(KeenPayError):
    status_code = 429
    code = "RATE_LIMITED"


class DependencyError(KeenPayError):
    """A service we depend on failed: database, Redis, Razorpay.

    502, not 500. The distinction is worth keeping: 500 means our bug, 502
    means someone else's outage, and confusing them sends on-call to the wrong
    place at three in the morning.
    """

    status_code = 502
    code = "DEPENDENCY_UNAVAILABLE"


class PaymentGateError(KeenPayError):
    """Raised when assert_payment_gates() fails before a Razorpay call.

    409 rather than 400: the request was well formed, but the session is not in
    a state where taking money is permitted.
    """

    status_code = 409
    code = "PAYMENT_GATES_FAILED"


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "DependencyError",
    "KeenPayError",
    "NotFoundError",
    "PaymentGateError",
    "RateLimitError",
    "ValidationError",
]
