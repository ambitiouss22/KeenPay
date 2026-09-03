"""Token bucket behaviour.

Time is injected rather than slept through. A test that calls ``sleep`` to
watch a bucket refill is slow and flaky on a loaded CI runner; a fake clock
makes the same assertions exact and instant.
"""

from __future__ import annotations

import threading

import pytest

from core.ratelimit import RateLimitPolicy, TokenBucketLimiter, bucket_key


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def limiter(clock: FakeClock, rate: int = 10, per: float = 60.0, capacity: int | None = None):
    return TokenBucketLimiter(
        RateLimitPolicy(rate=rate, per_seconds=per, capacity=capacity), clock=clock
    )


# --- basic budget -----------------------------------------------------------


def test_allows_up_to_capacity_then_refuses(clock):
    lim = limiter(clock, rate=5)
    assert [lim.check("t").allowed for _ in range(5)] == [True] * 5
    assert lim.check("t").allowed is False


def test_remaining_counts_down(clock):
    lim = limiter(clock, rate=3)
    assert lim.check("t").remaining == 2
    assert lim.check("t").remaining == 1
    assert lim.check("t").remaining == 0


def test_refuses_with_a_retry_after_that_actually_works(clock):
    lim = limiter(clock, rate=60, per=60.0)  # one token per second
    for _ in range(60):
        lim.check("t")

    denied = lim.check("t")
    assert denied.allowed is False
    assert denied.retry_after == pytest.approx(1.0, abs=0.01)

    # Waiting exactly as long as we were told must be enough.
    clock.advance(denied.retry_after)
    assert lim.check("t").allowed is True


def test_retry_after_header_is_a_whole_second_and_never_zero(clock):
    lim = limiter(clock, rate=60, per=60.0)
    for _ in range(60):
        lim.check("t")
    # 1.0s rounds to "1", not "0" - a Retry-After of 0 invites an instant retry
    # that is guaranteed to fail again.
    assert lim.check("t").retry_after_header == "1"


# --- refill -----------------------------------------------------------------


def test_refills_over_time(clock):
    lim = limiter(clock, rate=60, per=60.0)
    for _ in range(60):
        lim.check("t")
    assert lim.check("t").allowed is False

    clock.advance(10)
    assert [lim.check("t").allowed for _ in range(10)] == [True] * 10
    assert lim.check("t").allowed is False


def test_refill_is_capped_at_capacity(clock):
    """An idle hour must not bank an hour's worth of requests."""
    lim = limiter(clock, rate=10, per=60.0)
    lim.check("t")
    clock.advance(3600)
    assert [lim.check("t").allowed for _ in range(10)] == [True] * 10
    assert lim.check("t").allowed is False


def test_burst_capacity_is_separable_from_rate(clock):
    lim = limiter(clock, rate=10, per=60.0, capacity=20)
    assert [lim.check("t").allowed for _ in range(20)] == [True] * 20
    assert lim.check("t").allowed is False


# --- isolation --------------------------------------------------------------


def test_tenants_do_not_share_a_budget(clock):
    """The property the whole design exists for."""
    lim = limiter(clock, rate=3)
    for _ in range(3):
        assert lim.check("tenant:a").allowed is True
    assert lim.check("tenant:a").allowed is False
    assert lim.check("tenant:b").allowed is True


def test_scopes_do_not_share_a_budget(clock):
    lim = limiter(clock, rate=2)
    key_login = bucket_key(tenant_id=None, client_ip="1.2.3.4", scope="auth:login")
    key_api = bucket_key(tenant_id="t1", client_ip="1.2.3.4")
    for _ in range(2):
        lim.check(key_login)
    assert lim.check(key_login).allowed is False
    assert lim.check(key_api).allowed is True


# --- key construction -------------------------------------------------------


def test_key_prefers_tenant_over_ip():
    assert bucket_key(tenant_id="t1", client_ip="9.9.9.9") == "tenant:t1"


def test_key_falls_back_to_ip_when_unauthenticated():
    assert bucket_key(tenant_id=None, client_ip="9.9.9.9") == "ip:9.9.9.9"


def test_key_handles_a_missing_client():
    assert bucket_key(tenant_id=None, client_ip=None) == "ip:unknown"


def test_one_tenant_cannot_borrow_another_budget_by_changing_ip():
    """Rotating IPs must not reset a tenant's allowance."""
    a = bucket_key(tenant_id="t1", client_ip="1.1.1.1")
    b = bucket_key(tenant_id="t1", client_ip="2.2.2.2")
    assert a == b


# --- housekeeping and safety ------------------------------------------------


def test_reset_clears_one_key_only(clock):
    lim = limiter(clock, rate=1)
    lim.check("a")
    lim.check("b")
    lim.reset("a")
    assert lim.check("a").allowed is True
    assert lim.check("b").allowed is False


def test_prune_drops_only_stale_buckets(clock):
    lim = limiter(clock, rate=5)
    lim.check("old")
    clock.advance(7200)
    lim.check("fresh")
    assert lim.prune(older_than=3600) == 1
    assert "old" not in lim._buckets
    assert "fresh" in lim._buckets


@pytest.mark.parametrize(
    "kwargs",
    [{"rate": 0}, {"rate": -1}, {"rate": 5, "per_seconds": 0}, {"rate": 5, "capacity": 0}],
)
def test_nonsense_policies_are_rejected(kwargs):
    with pytest.raises(ValueError):
        RateLimitPolicy(**kwargs)


def test_concurrent_checks_cannot_overspend(clock):
    """Two threads must not both spend the same last token."""
    lim = limiter(clock, rate=50)
    allowed: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        got = lim.check("shared").allowed
        with lock:
            allowed.append(got)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(allowed) == 50
