"""Request pipeline: request id, tenant context, rate limiting, logging.

Collected in one module because the order they run in is part of their meaning,
and that is easier to reason about in one place than spread across four files.
Outermost to innermost:

    RequestID  ->  TenantContext  ->  RateLimit  ->  Logging  ->  route

RequestID is outermost so that every later layer, including a rejection, can
put the same id on the response. TenantContext precedes RateLimit because the
limiter is keyed per tenant and needs the identity resolved first. Logging is
innermost so it measures the handler rather than the queueing in front of it.

The security-critical piece is :class:`TenantContextMiddleware`, and
specifically that it *deletes* inbound tenant headers. Reading tenant identity
only from a verified token is a rule that has to hold in every handler written
from now on; removing the header makes it hold whether or not the next person
remembers it, because there is no longer anything there to read.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.jwt import JWTManager, TokenError
from core.ratelimit import RateLimitPolicy, TokenBucketLimiter, bucket_key

logger = structlog.get_logger(__name__)

#: Headers a client might use to assert a tenant. Stripped on every request.
#: If a reverse proxy in front of this app ever needs to inject a trusted
#: tenant, it must use a name that is NOT in this list and the code that reads
#: it must verify the proxy's identity — otherwise anyone can send it.
SPOOFABLE_TENANT_HEADERS = (
    "x-tenant-id",
    "x-tenant",
    "x-merchant-id",
    "x-merchant",
    "x-org-id",
    "x-account-id",
    "tenant-id",
    "merchant-id",
)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request id to state and echo it on the response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        # A client-supplied id is fine for tracing but must not be trusted as
        # data: cap it and keep it printable so it cannot inject into logs.
        if incoming and len(incoming) <= 128 and incoming.isprintable():
            request_id = incoming
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Establish tenant identity from the bearer token, and only from it.

    Two jobs:

    1. Remove any inbound header by which a client could claim a tenant. This
       is the part that makes spoofing impossible rather than merely unused.
    2. Best-effort decode of the bearer token to publish ``request.state``
       fields for the layers that need identity before route dependencies run,
       namely rate limiting and logging.

    A bad or missing token is *not* rejected here. Authentication is enforced
    by the route dependency, which knows which routes are public; rejecting
    here would break ``/health`` and ``/auth/login``. So this middleware only
    ever populates context — it never grants access.
    """

    def __init__(self, app, *, jwt_manager: JWTManager | None = None) -> None:
        super().__init__(app)
        self._jwt = jwt_manager or JWTManager()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Starlette exposes raw headers as a list of (bytes, bytes). Rebuilding
        # it without the spoofable names means anything reading the headers
        # later — handlers, other middleware, logging — simply cannot see them.
        stripped = [
            (k, v)
            for (k, v) in request.scope.get("headers", [])
            if k.decode("latin-1").lower() not in SPOOFABLE_TENANT_HEADERS
        ]
        if len(stripped) != len(request.scope.get("headers", [])):
            logger.warning(
                "tenant_header_stripped",
                path=request.url.path,
                client=request.client.host if request.client else None,
            )
        request.scope["headers"] = stripped

        request.state.tenant_id = None
        request.state.merchant_id = None
        request.state.user_id = None
        request.state.role = None

        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                claims = self._jwt.decode_access_token(auth[7:].strip())
            except TokenError:
                # Expired or forged. Leave the context empty and let the route
                # dependency produce the 401 with a proper error envelope.
                pass
            else:
                request.state.tenant_id = claims.tenant_id
                request.state.merchant_id = claims.merchant_id
                request.state.user_id = claims.sub
                request.state.role = claims.role
                structlog.contextvars.bind_contextvars(
                    tenant_id=claims.tenant_id or claims.merchant_id,
                    user_id=claims.sub,
                )

        try:
            return await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("tenant_id", "user_id")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-tenant token bucket, with a tighter budget on auth routes.

    Authentication endpoints get their own much smaller bucket keyed by IP,
    because there is no tenant yet and they are the endpoints worth brute
    forcing. Keeping that budget separate means an attacker hammering login
    cannot also exhaust a tenant's ordinary API allowance.
    """

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int = 120,
        login_per_minute: int = 10,
        auth_per_minute: int = 30,
    ) -> None:
        super().__init__(app)
        self._general = TokenBucketLimiter(RateLimitPolicy(rate=requests_per_minute))
        self._login = TokenBucketLimiter(RateLimitPolicy(rate=login_per_minute))
        self._auth = TokenBucketLimiter(RateLimitPolicy(rate=auth_per_minute))

    def _select(self, path: str) -> tuple[TokenBucketLimiter, str, bool]:
        """Return (limiter, scope, force_ip_key) for a path."""
        if path.startswith("/api/v1/auth/login"):
            return self._login, "auth:login", True
        if path.startswith("/api/v1/auth/"):
            return self._auth, "auth", True
        return self._general, "", False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        limiter, scope, force_ip = self._select(request.url.path)
        client_ip = request.client.host if request.client else None
        tenant_id = None if force_ip else getattr(request.state, "tenant_id", None)
        if not force_ip and tenant_id is None:
            # Authenticated-but-legacy tokens carry no tenant_id; the merchant
            # slug is equally token-derived, so it is a sound fallback key.
            tenant_id = getattr(request.state, "merchant_id", None)

        key = bucket_key(tenant_id=tenant_id, client_ip=client_ip, scope=scope)
        decision = limiter.check(key)

        if not decision.allowed:
            logger.warning("rate_limited", path=request.url.path, key=key)
            # JSONResponse rather than raising HTTPException: an exception
            # raised inside BaseHTTPMiddleware bypasses the app's exception
            # handlers and surfaces as a 500.
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                    }
                },
                headers={"Retry-After": decision.retry_after_header},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """One structured line per request, with duration and outcome."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise

        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response


__all__ = [
    "REQUEST_ID_HEADER",
    "SPOOFABLE_TENANT_HEADERS",
    "LoggingMiddleware",
    "RateLimitMiddleware",
    "RequestIDMiddleware",
    "TenantContextMiddleware",
]
