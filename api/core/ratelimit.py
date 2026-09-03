"""Per-tenant token-bucket rate limiting.

A token bucket rather than a fixed window, because a fixed window lets a caller
spend its whole allowance in the last second of one window and again in the
first second of the next — a 2x burst straddling the boundary. A bucket refills
continuously, so the rate holds over every interval, while still allowing a
short burst up to ``capacity``.

Keyed per tenant, not per IP. IP is the wrong unit here: one tenant behind a
corporate NAT would throttle its own users against each other, and an attacker
with a pool of addresses would bypass the limit entirely. The tenant comes from
the verified token, so it cannot be forged to get a fresh allowance.

The limiter is pure and synchronous — no clock of its own, no I/O. That is what
makes the behaviour testable without sleeping through real time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Decision:
    """Outcome of one rate-limit check."""

    allowed: bool
    remaining: int
    #: Seconds until the next token is available. 0 when allowed.
    retry_after: float

    @property
    def retry_after_header(self) -> str:
        """``Retry-After`` is defined in whole seconds, and must be >= 1."""
        return str(max(1, int(self.retry_after + 0.999)))


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimitPolicy:
    """How many requests, over what period, with how much burst.

    ``capacity`` defaults to ``rate`` so a caller may burst up to one full
    period's worth at once and then settles to the steady rate.
    """

    rate: int
    per_seconds: float = 60.0
    capacity: int | None = None

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError("rate must be positive")
        if self.per_seconds <= 0:
            raise ValueError("per_seconds must be positive")
        if self.capacity is None:
            self.capacity = self.rate
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")

    @property
    def refill_per_second(self) -> float:
        return self.rate / self.per_seconds


@dataclass
class TokenBucketLimiter:
    """In-process token buckets, one per key.

    Safe under concurrency: a lock guards the whole read-modify-write, so two
    requests arriving together cannot both observe the same last token and both
    be allowed.

    In-process means per-worker. Running N workers multiplies the effective
    limit by N; that is acceptable for a coarse abuse guard and is the reason
    this is a guard rather than a quota. A Redis-backed store is the drop-in
    replacement when the limit needs to be exact across workers.
    """

    policy: RateLimitPolicy
    #: Injectable for tests. Must be monotonic — a wall clock that steps
    #: backwards (NTP correction) would hand out free tokens.
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self, key: str, *, cost: float = 1.0) -> Decision:
        """Consume ``cost`` tokens for ``key`` if available."""
        now = self.clock()
        capacity = float(self.policy.capacity)

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=capacity, updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * self.policy.refill_per_second)
            bucket.updated_at = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return Decision(True, int(bucket.tokens), 0.0)

            deficit = cost - bucket.tokens
            return Decision(False, 0, deficit / self.policy.refill_per_second)

    def reset(self, key: str | None = None) -> None:
        """Drop state for one key, or all of it. For tests and admin recovery."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def prune(self, *, older_than: float = 3600.0) -> int:
        """Drop buckets untouched for a while, and return how many went.

        Without this the dict grows once per distinct key seen — a slow leak
        that a long-lived process eventually notices.
        """
        cutoff = self.clock() - older_than
        with self._lock:
            stale = [k for k, b in self._buckets.items() if b.updated_at < cutoff]
            for k in stale:
                del self._buckets[k]
        return len(stale)


def bucket_key(*, tenant_id: str | None, client_ip: str | None, scope: str = "") -> str:
    """Build the limiter key for a request.

    Tenant first: it is verified, stable, and the unit the limit is expressed
    in. IP is only the fallback for unauthenticated traffic — login, refresh —
    where there is no tenant yet and IP is the only handle available.

    ``scope`` separates limits that must not share a budget, so that hammering
    login cannot exhaust the allowance for the rest of the API.
    """
    principal = f"tenant:{tenant_id}" if tenant_id else f"ip:{client_ip or 'unknown'}"
    return f"{principal}|{scope}" if scope else principal


__all__ = [
    "Decision",
    "RateLimitPolicy",
    "TokenBucketLimiter",
    "bucket_key",
]
