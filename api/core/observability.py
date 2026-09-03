"""Metrics and tracing hooks.

Deliberately thin. Phase 17 chooses the backend; this is the seam that keeps
call sites from having to change when it does. Everything here is a no-op you
can call unconditionally - so instrumentation is added where the behaviour is,
rather than being deferred until "the observability phase", by which time
nobody remembers which paths mattered.

The Prometheus counters are process-local. With several workers each holds its
own view; the scrape aggregates them. That is normal for Prometheus and is why
these are counters and histograms rather than gauges of absolute truth.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

# --- metrics ----------------------------------------------------------------
# Labels are deliberately low-cardinality. Putting a user id, session id or raw
# path with ids in it on a label multiplies the time series until the metrics
# store falls over - the classic way to take Prometheus down with your own app.

REQUESTS = Counter(
    "keenpay_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)

REQUEST_SECONDS = Histogram(
    "keenpay_request_duration_seconds",
    "Time spent handling a request",
    ["method", "route"],
)

DEPENDENCY_SECONDS = Histogram(
    "keenpay_dependency_duration_seconds",
    "Time spent calling a dependency",
    ["dependency", "operation"],
)

DEPENDENCY_FAILURES = Counter(
    "keenpay_dependency_failures_total",
    "Dependency calls that raised",
    ["dependency", "operation"],
)

BUSINESS_EVENTS = Counter(
    "keenpay_business_events_total",
    "Notable domain events",
    ["event"],
)


def record_request(*, method: str, route: str, status: int, duration: float) -> None:
    """Record one handled request.

    ``route`` must be the *template* (``/api/v1/orders/{order_id}``), never the
    concrete path. Concrete paths would create one time series per order id.
    """
    REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    REQUEST_SECONDS.labels(method=method, route=route).observe(duration)


@contextmanager
def track_dependency(dependency: str, operation: str) -> Iterator[None]:
    """Time a dependency call and count its failures.

        with track_dependency("postgres", "get_order"):
            ...

    Timing is recorded whether or not the call raised: a slow failure is a fact
    worth having, and dropping it would make latency look better during an
    outage than in normal operation.
    """
    started = time.perf_counter()
    try:
        yield
    except Exception:
        DEPENDENCY_FAILURES.labels(dependency=dependency, operation=operation).inc()
        raise
    finally:
        DEPENDENCY_SECONDS.labels(dependency=dependency, operation=operation).observe(
            time.perf_counter() - started
        )


def record_event(event: str) -> None:
    """Count a domain event: ``order_created``, ``payment_captured``."""
    BUSINESS_EVENTS.labels(event=event).inc()


@contextmanager
def span(name: str, **attributes) -> Iterator[dict]:
    """Tracing seam.

    A no-op that yields its attribute dict, so call sites can be written now
    and become real spans when a tracer is wired in Phase 17 - without touching
    the code that uses them.
    """
    attrs = dict(attributes)
    attrs["span.name"] = name
    yield attrs


__all__ = [
    "BUSINESS_EVENTS",
    "DEPENDENCY_FAILURES",
    "DEPENDENCY_SECONDS",
    "REQUESTS",
    "REQUEST_SECONDS",
    "record_event",
    "record_request",
    "span",
    "track_dependency",
]
