"""Provider factory and raw-status mapping.

The factory imports its concrete providers *inside* :func:`get_provider`, not at
module level. ``services.razorpay`` imports :func:`canonical_state` from here, so
a top-level import of it would close a cycle and crash on load. Keeping the
mapping free of any dependency on the provider implementations is what lets both
directions work.
"""

from modules.payments.state import PaymentState

RAW_TO_STATE = {
    "created": PaymentState.CREATED,
    "attempted": PaymentState.AUTH_REQUIRED,
    "authorized": PaymentState.AUTHORIZED,
    "authorised": PaymentState.AUTHORIZED,
    "captured": PaymentState.CAPTURED,
    "failed": PaymentState.FAILED,
    "refunded": PaymentState.REFUNDED,
    "partially_refunded": PaymentState.PARTIALLY_REFUNDED,
}


def canonical_state(raw_status: str | None) -> tuple[PaymentState, bool]:
    """Map a provider status to a canonical state.

    Returns ``(state, unrecognised)``. An unknown status becomes UNKNOWN and is
    flagged, never quietly mapped to a convenient default: "we do not know" is a
    different answer from "it failed", and only one of them is safe to act on.
    """
    if not raw_status:
        return PaymentState.UNKNOWN, False

    normalised = (raw_status or "").strip().lower()
    if normalised in RAW_TO_STATE:
        return RAW_TO_STATE[normalised], False

    return PaymentState.UNKNOWN, True


def get_provider():
    """Return the live provider when keys are configured, else the mock."""
    from config.settings import get_settings
    from services.razorpay import RazorpayProvider
    from services.razorpay_mock import RazorpayMockProvider

    settings = get_settings()
    if settings.razorpay_key_id and settings.razorpay_key_secret and not settings.razorpay_mock:
        return RazorpayProvider()
    return RazorpayMockProvider()
