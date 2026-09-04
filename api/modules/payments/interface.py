"""Payment provider protocol and exceptions."""

from dataclasses import dataclass
from typing import Protocol

from modules.payments.state import PaymentState


class ProviderError(Exception):
    """Provider returned a definite failure (4xx)."""
    def __init__(self, code: str = "PROVIDER_ERROR", message: str = "", retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


class ProviderTimeout(ProviderError):
    """Provider timeout or 5xx (unknown outcome)."""
    def __init__(self):
        super().__init__(code="PROVIDER_TIMEOUT", message="Timeout or 5xx", retryable=False)


@dataclass(frozen=True)
class ProviderResult:
    """Immutable result from a provider operation."""
    provider_payment_id: str
    provider_order_id: str
    state: PaymentState
    raw_status: str
    unrecognised: bool = False


class PaymentProvider(Protocol):
    """Provider interface - all providers must implement this."""

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        reference_id: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Create a payment order."""
        ...

    async def capture(
        self,
        provider_payment_id: str,
        amount_paise: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Capture a payment."""
        ...

    async def refund(
        self,
        provider_payment_id: str,
        amount_paise: int,
        idempotency_key: str,
    ) -> ProviderResult:
        """Refund a payment."""
        ...

    async def get_status(
        self,
        provider_payment_id: str,
    ) -> ProviderResult:
        """Get current payment status."""
        ...
