"""Razorpay clients.

Two clients live here, and they are separate on purpose.

:class:`RazorpayService` is the payment-link client the checkout session flow
has used since phase 3. :class:`RazorpayProvider` is the phase 6 payment
engine's client, and the one distinction it exists to preserve is that a 4xx is
a definite "no" (:class:`ProviderError`) while a timeout or 5xx is "we do not
know" (:class:`ProviderTimeout`). Collapsing the two is how a payment that
actually went through gets retried.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from config.settings import get_settings
from core.security import assert_payment_gates
from modules.payments.interface import ProviderError, ProviderResult, ProviderTimeout
from modules.payments.provider import canonical_state
from modules.payments.state import PaymentState

_TIMEOUT_SECONDS = 10
_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayService:
    """Payment-link client used by the checkout session flow."""

    def __init__(self, webhook_secret: str | None = None):
        # An explicit secret overrides settings *and* the mock shortcut, so a
        # test can exercise real signature checking without the rest of the
        # suite needing live credentials. Left unset, behaviour is unchanged.
        self._secret_override = webhook_secret

    async def create_payment_link(
        self,
        *,
        state: dict[str, Any],
        amount_paise: int,
        description: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        assert_payment_gates(state)
        settings = get_settings()
        if settings.razorpay_mock:
            from services.razorpay_mock import RazorpayMockService

            return await RazorpayMockService().create_payment_link(
                amount_paise=amount_paise,
                description=description,
                idempotency_key=idempotency_key,
            )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_BASE_URL}/payment_links",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": description,
                    "reference_id": idempotency_key,
                },
                headers={"X-Razorpay-Idempotency-Key": idempotency_key},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "payment_link_id": data["id"],
                "payment_link_url": data["short_url"],
                "expires_at": data.get("expire_by"),
            }

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        """Constant-time HMAC check over the raw body.

        The raw bytes matter: re-serialising the JSON first changes the bytes
        and the signature stops matching. An empty secret refuses everything
        rather than accepting everything — no secret must not mean no checking.
        """
        if self._secret_override is None:
            settings = get_settings()
            if settings.razorpay_mock:
                return True
            secret = settings.razorpay_webhook_secret
        else:
            secret = self._secret_override

        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class RazorpayProvider:
    """Live Razorpay client for the payment engine."""

    def __init__(self):
        settings = get_settings()
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise ValueError("Razorpay credentials are not configured")
        self._key_id = settings.razorpay_key_id
        self._key_secret = settings.razorpay_key_secret
        self._base_url = _BASE_URL

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        reference_id: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Create a payment order."""
        data = await self._post(
            "/orders",
            json={"amount": amount_paise, "currency": currency, "receipt": reference_id},
            idempotency_key=idempotency_key,
            default_error="ORDER_CREATE_FAILED",
        )
        state, unrecognised = canonical_state(data.get("status"))
        return ProviderResult(
            provider_payment_id=data.get("id", ""),
            provider_order_id=data.get("id", ""),
            state=state,
            raw_status=data.get("status", ""),
            unrecognised=unrecognised,
        )

    async def capture(
        self,
        provider_payment_id: str,
        amount_paise: int,
        currency: str,
        idempotency_key: str,
    ) -> ProviderResult:
        """Capture an authorized payment."""
        data = await self._post(
            f"/payments/{provider_payment_id}/capture",
            json={"amount": amount_paise, "currency": currency},
            idempotency_key=idempotency_key,
            default_error="CAPTURE_FAILED",
        )
        state, unrecognised = canonical_state(data.get("status"))
        return ProviderResult(
            provider_payment_id=data.get("id", provider_payment_id),
            provider_order_id=data.get("order_id", ""),
            state=state,
            raw_status=data.get("status", ""),
            unrecognised=unrecognised,
        )

    async def refund(
        self,
        provider_payment_id: str,
        amount_paise: int,
        idempotency_key: str,
    ) -> ProviderResult:
        """Refund a captured payment."""
        data = await self._post(
            f"/payments/{provider_payment_id}/refund",
            json={"amount": amount_paise},
            idempotency_key=idempotency_key,
            default_error="REFUND_FAILED",
        )
        # Refund status is its own vocabulary; mapping it onto payment states
        # would invent a certainty the response does not carry.
        return ProviderResult(
            provider_payment_id=data.get("id", ""),
            provider_order_id=data.get("payment_id", ""),
            state=PaymentState.UNKNOWN,
            raw_status=data.get("status", ""),
        )

    async def get_status(self, provider_payment_id: str) -> ProviderResult:
        """Read the provider's current view of a payment."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self._base_url}/payments/{provider_payment_id}",
                    auth=(self._key_id, self._key_secret),
                    timeout=_TIMEOUT_SECONDS,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise ProviderTimeout() from exc

        data = self._decode(response, default_error="PAYMENT_NOT_FOUND")
        state, unrecognised = canonical_state(data.get("status"))
        return ProviderResult(
            provider_payment_id=data.get("id", provider_payment_id),
            provider_order_id=data.get("order_id", ""),
            state=state,
            raw_status=data.get("status", ""),
            unrecognised=unrecognised,
        )

    # --- transport ----------------------------------------------------------

    async def _post(
        self,
        path: str,
        *,
        json: dict,
        idempotency_key: str,
        default_error: str,
    ) -> dict:
        """POST once, translating transport failure into ProviderTimeout."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self._base_url}{path}",
                    auth=(self._key_id, self._key_secret),
                    json=json,
                    headers={"X-Razorpay-Idempotency-Key": idempotency_key},
                    timeout=_TIMEOUT_SECONDS,
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise ProviderTimeout() from exc

        return self._decode(response, default_error=default_error)

    @staticmethod
    def _decode(response: httpx.Response, *, default_error: str) -> dict:
        """Turn a response into data, a definite error, or an unknown outcome."""
        if response.status_code >= 500:
            raise ProviderTimeout()

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderTimeout() from exc

        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            raise ProviderError(
                code=error.get("code", default_error),
                message=error.get("description", ""),
            )
        return data if isinstance(data, dict) else {}


__all__ = ["RazorpayProvider", "RazorpayService"]
