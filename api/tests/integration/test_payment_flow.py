"""The payment path end to end: order in, money taken, exactly once."""


from modules.payments.state import PaymentState
from repositories.orders import OrderRepository
from services.payments import PaymentService
from services.razorpay_mock import Behaviour, RazorpayMockProvider

MERCHANT = "merchant_1"
KEY = "pay-key-abcdefghij"


def _line(sku="X", price=100, cost=50, quantity=1):
    return {
        "sku": sku,
        "name": sku,
        "quantity": quantity,
        "list_price_paise": price,
        "cost_paise": cost,
    }


async def _order(amount_paise: int = 100, merchant_id: str = MERCHANT) -> dict:
    """A pending order in the shape the snapshot can hash."""
    return await OrderRepository().create_pending(
        session_id="00000000-0000-0000-0000-000000000001",
        merchant_id=merchant_id,
        user_id="user_1",
        line_items=[_line(price=amount_paise)],
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


def _service(behaviour: Behaviour | None = None) -> PaymentService:
    return PaymentService(provider=RazorpayMockProvider(behaviour))


async def _pay(service, order, *, key=KEY):
    return await service.create_payment(
        merchant_id=MERCHANT,
        order_id=order["id"],
        authorization_id="auth_1",
        idempotency_key=key,
        request_body={"order_id": order["id"], "authorization_id": "auth_1"},
    )


async def test_a_paid_order_ends_captured():
    order = await _order()
    result = await _pay(_service(), order)

    assert result["status_code"] == 201, result
    assert result["body"]["status"] == PaymentState.CAPTURED.value
    assert result["body"]["captured_paise"] == 100


async def test_the_amount_comes_from_the_order():
    order = await _order(amount_paise=4200)
    result = await _pay(_service(), order)
    assert result["body"]["amount_paise"] == 4200


async def test_the_payment_carries_the_snapshot_it_was_bound_to():
    order = await _order()
    result = await _pay(_service(), order)
    assert result["body"]["order_snapshot_hash"]
    assert result["body"]["order_snapshot"]["final_amount_paise"] == 100


async def test_the_same_key_returns_the_same_payment():
    """A retry must replay, not charge again."""
    order = await _order()
    service = _service()

    first = await _pay(service, order)
    second = await _pay(service, order)

    assert first["body"]["id"] == second["body"]["id"]
    assert second["status_code"] == 201


async def test_a_different_key_on_the_same_order_is_a_second_payment():
    order = await _order()
    service = _service()
    first = await _pay(service, order, key=KEY)
    second = await _pay(service, order, key=KEY + "z")
    assert first["body"]["id"] != second["body"]["id"]


async def test_the_same_key_with_a_different_body_is_refused():
    order = await _order()
    other = await _order()
    service = _service()

    await _pay(service, order)
    clash = await service.create_payment(
        merchant_id=MERCHANT,
        order_id=other["id"],
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": other["id"], "authorization_id": "auth_1"},
    )
    assert clash["status_code"] == 409


async def test_an_unknown_order_is_404():
    result = await _service().create_payment(
        merchant_id=MERCHANT,
        order_id="ord_nope",
        authorization_id="auth_1",
        idempotency_key=KEY,
        request_body={"order_id": "ord_nope"},
    )
    assert result["status_code"] == 404


async def test_another_merchants_order_is_also_404():
    """Same answer as a missing order: confirming it exists maps their ids."""
    order = await _order(merchant_id="merchant_other")
    result = await _pay(_service(), order)
    assert result["status_code"] == 404


async def test_a_timeout_leaves_the_payment_unknown_not_failed():
    """The headline property: we do not claim to know what we do not know."""
    order = await _order()
    service = _service(Behaviour(capture="timeout"))

    result = await _pay(service, order)
    assert result["status_code"] == 502
    assert result["body"]["error"]["code"] == "PROVIDER_UNKNOWN"


async def test_a_timeout_does_not_free_the_key():
    """Freeing it would invite the retry that double-charges."""
    order = await _order()
    service = _service(Behaviour(capture="timeout"))

    await _pay(service, order)
    retry = await _pay(service, order)
    assert retry["status_code"] == 409


async def test_a_refusal_is_reported_as_a_refusal():
    order = await _order()
    result = await _pay(_service(Behaviour(capture="error")), order)
    assert result["status_code"] == 402


async def test_an_unrecognised_capture_status_is_unknown():
    order = await _order()
    result = await _pay(_service(Behaviour(capture="teleported")), order)
    assert result["status_code"] == 502


async def test_status_reports_settlement():
    order = await _order()
    service = _service()
    created = await _pay(service, order)

    status = await service.get_status(created["body"]["id"], merchant_id=MERCHANT)
    assert status["settled"] is True


async def test_status_for_an_unknown_payment_is_none():
    assert await _service().get_status("pay_nope", merchant_id=MERCHANT) is None


async def test_a_refund_moves_the_payment_to_refunded():
    order = await _order()
    service = _service()
    created = await _pay(service, order)

    refund = await service.refund_payment(
        merchant_id=MERCHANT,
        payment_id=created["body"]["id"],
        authorization_id="auth_2",
        amount_paise=100,
        idempotency_key="refund-key-abcdefg1",
    )
    assert refund["status_code"] == 200
    assert refund["body"]["status"] == PaymentState.REFUNDED.value


async def test_a_partial_refund_says_so():
    order = await _order(amount_paise=1000)
    service = _service()
    created = await _pay(service, order)

    refund = await service.refund_payment(
        merchant_id=MERCHANT,
        payment_id=created["body"]["id"],
        authorization_id="auth_2",
        amount_paise=400,
        idempotency_key="refund-key-abcdefg2",
    )
    assert refund["body"]["status"] == PaymentState.PARTIALLY_REFUNDED.value
    assert refund["body"]["refunded_paise"] == 400


async def test_a_refund_larger_than_the_capture_is_refused():
    order = await _order(amount_paise=100)
    service = _service()
    created = await _pay(service, order)

    refund = await service.refund_payment(
        merchant_id=MERCHANT,
        payment_id=created["body"]["id"],
        authorization_id="auth_2",
        amount_paise=101,
        idempotency_key="refund-key-abcdefg3",
    )
    assert refund["status_code"] == 422
    assert refund["body"]["error"]["code"] == "REFUND_EXCEEDS_CAPTURE"


async def test_two_partial_refunds_cannot_exceed_the_capture_together():
    """The one an amount-per-call check misses."""
    order = await _order(amount_paise=1000)
    service = _service()
    created = await _pay(service, order)
    payment_id = created["body"]["id"]

    await service.refund_payment(
        merchant_id=MERCHANT,
        payment_id=payment_id,
        authorization_id="auth_2",
        amount_paise=600,
        idempotency_key="refund-key-abcdefg4",
    )
    second = await service.refund_payment(
        merchant_id=MERCHANT,
        payment_id=payment_id,
        authorization_id="auth_2",
        amount_paise=600,
        idempotency_key="refund-key-abcdefg5",
    )
    assert second["status_code"] == 422
