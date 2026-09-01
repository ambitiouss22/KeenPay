"""Razorpay payment link client."""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import get_settings
from core.security import assert_payment_gates


class RazorpayService:
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
                "https://api.razorpay.com/v1/payment_links",
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

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        import hashlib
        import hmac

        settings = get_settings()
        if settings.razorpay_mock:
            return True
        expected = hmac.new(
            settings.razorpay_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
