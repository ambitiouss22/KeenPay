"""Attacks the payment path is written to refuse."""

import pytest

from repositories.orders import OrderRepository
from repositories.payments import reset_payments
from services.payments import PaymentService
from services.razorpay_mock import RazorpayMockProvider

MERCHANT = "merchant_1"
KEY = "pay-key-abcdefghij"


@pytest.fixture(autouse=True)
def _clean():
    from repositories.idempotency import reset_idempotency
    from repositories.outbox import reset_outbox

    reset_payments()
    reset_idempotency()
    reset_outbox()
    yield
    reset_payments()
    reset_idempotency()
    reset_outbox()


async def _order(amount_paise: int = 100, merchant_id: str = MERCHANT) -> dict:
    return await OrderRepository().create_pending(
        session_id="00000000-0000-0000-0000-000000000001",
        merchant_id=merchant_id,
        user_id="user_1",
        line_items=[
            {
                "sku": "X",
                "name": "X",
                "quantity": 1,
                "list_price_paise": amount_paise,
                "cost_paise": 50,
            }
        ],
        subtotal_paise=amount_paise,
        discount_amount_paise=0,
        final_amount_paise=amount_paise,
        guardrail_decision_id="00000000-0000-0000-0000-000000000002",
        offer_version=1,
        policy_version="test",
        idempotency_key="checkout-key-abcdefgh",
        razorpay_payment_link_id="",
        razorpay_payment_link_url="",
    )


def _service() -> PaymentService:
    return PaymentService(provider=RazorpayMockProvider())


async def test_an_amount_in_the_body_is_ignored():
    """The price-tampering attempt. The order decides, always."""
    order = await _order(amount_paise=100)

    result = await _service().create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={
            "order_id": order["id"],
            "authorization_id": "auth_1",
            "amount_paise": 1,
        },
    )

    assert result["body"]["amount_paise"] == 100
    assert result["body"]["captured_paise"] == 100


async def test_another_merchant_cannot_read_the_payment():
    """404, not 403: a 403 confirms the id is real."""
    order = await _order()
    service = _service()
    created = await service.create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": order["id"], "authorization_id": "auth_1"},
    )

    assert await service.get_payment(created["body"]["id"], merchant_id="merchant_2") is None


async def test_another_merchant_cannot_refund_the_payment():
    order = await _order()
    service = _service()
    created = await service.create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": order["id"], "authorization_id": "auth_1"},
    )

    stolen = await service.refund_payment(
        merchant_id="merchant_2",
        payment_id=created["body"]["id"],
        authorization_id="auth_2",
        amount_paise=100,
        idempotency_key="refund-key-abcdefg9",
    )
    assert stolen["status_code"] == 404


async def test_a_cart_mutated_after_checkout_changes_the_bound_hash():
    """Swapping goods at the same total must not reuse the old snapshot."""
    from modules.payments.snapshot import assert_snapshot_unchanged

    order = await _order(amount_paise=100)
    created = await _service().create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": order["id"], "authorization_id": "auth_1"},
    )
    bound = created["body"]["order_snapshot_hash"]

    swapped = dict(order)
    swapped["line_items"] = [
        {"sku": "GOLD", "name": "GOLD", "quantity": 1, "list_price_paise": 100, "cost_paise": 50}
    ]

    with pytest.raises(ValueError):
        assert_snapshot_unchanged(bound, swapped)


async def test_a_float_price_never_reaches_the_provider():
    """249.9 truncating to 249 is how money goes missing."""
    order = await OrderRepository().create_pending(
        session_id="00000000-0000-0000-0000-000000000001",
        merchant_id=MERCHANT,
        user_id="user_1",
        line_items=[
            {
                "sku": "X",
                "name": "X",
                "quantity": 1,
                "list_price_paise": 249.9,
                "cost_paise": 50,
            }
        ],
        subtotal_paise=249,
        discount_amount_paise=0,
        final_amount_paise=249,
        guardrail_decision_id="00000000-0000-0000-0000-000000000002",
        offer_version=1,
        policy_version="test",
        idempotency_key="checkout-key-abcdefgh",
        razorpay_payment_link_id="",
        razorpay_payment_link_url="",
    )

    result = await _service().create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": order["id"], "authorization_id": "auth_1"},
    )
    assert result["status_code"] == 422
    assert result["body"]["error"]["code"] == "ORDER_NOT_PAYABLE"
