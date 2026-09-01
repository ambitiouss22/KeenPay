"""Mock Razorpay for local dev and tests."""

from uuid import uuid4


class RazorpayMockService:
    async def create_payment_link(
        self,
        *,
        amount_paise: int,
        description: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        link_id = f"plink_mock_{uuid4().hex[:12]}"
        return {
            "payment_link_id": link_id,
            "payment_link_url": f"https://rzp.io/mock/{link_id}",
            "expires_at": None,
        }

    async def simulate_payment(self, payment_link_id: str) -> dict[str, str]:
        return {"payment_id": f"pay_mock_{uuid4().hex[:12]}", "payment_link_id": payment_link_id}
