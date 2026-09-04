"""Claim-first idempotency."""


from modules.idempotency.service import IdempotencyService, IdempotencyVerdict

ENDPOINT = "/payments"
KEY = "key-abcdefghijklmnop"


async def test_a_new_key_is_claimed():
    verdict = await IdempotencyService().claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    assert verdict is IdempotencyVerdict.CLAIMED


async def test_a_second_claim_while_in_flight_is_refused():
    """The window a retry would otherwise slip through."""
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    verdict = await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    assert verdict is IdempotencyVerdict.IN_PROGRESS


async def test_a_finished_key_replays():
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    await service.complete("m1", ENDPOINT, KEY, 201, {"id": "pay_1"})

    assert await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"}) is (
        IdempotencyVerdict.REPLAY
    )
    assert await service.replay_response("m1", ENDPOINT, KEY) == (201, {"id": "pay_1"})


async def test_the_same_key_with_a_different_body_is_a_conflict():
    """Reusing a key for a different request is a client bug, not a retry."""
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    verdict = await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_2"})
    assert verdict is IdempotencyVerdict.CONFLICT


async def test_keys_are_scoped_per_merchant():
    """Two merchants picking the same key must not collide."""
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    await service.complete("m1", ENDPOINT, KEY, 201, {"id": "pay_1"})
    verdict = await service.claim("m2", ENDPOINT, KEY, {"order_id": "ord_2"})
    assert verdict is IdempotencyVerdict.CLAIMED


async def test_keys_are_scoped_per_endpoint():
    service = IdempotencyService()
    await service.claim("m1", "/payments", KEY, {"order_id": "ord_1"})
    verdict = await service.claim("m1", "/payments/refund", KEY, {"order_id": "ord_1"})
    assert verdict is IdempotencyVerdict.CLAIMED


async def test_release_frees_an_unfinished_key():
    """Only pre-provider failures release; the key must be usable again after."""
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    await service.release("m1", ENDPOINT, KEY)
    assert await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"}) is (
        IdempotencyVerdict.CLAIMED
    )


async def test_release_does_not_free_a_finished_key():
    """A completed key must keep replaying, or a retry charges twice."""
    service = IdempotencyService()
    await service.claim("m1", ENDPOINT, KEY, {"order_id": "ord_1"})
    await service.complete("m1", ENDPOINT, KEY, 201, {"id": "pay_1"})
    await service.release("m1", ENDPOINT, KEY)
    assert await service.replay_response("m1", ENDPOINT, KEY) is not None
