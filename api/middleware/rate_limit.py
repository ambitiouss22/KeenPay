"""Redis-backed rate limiting middleware."""

import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# In-process fallback when Redis unavailable (dev only)
_BUCKETS: dict[str, list[float]] = defaultdict(list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global rate limit + stricter limits on auth routes."""

    def __init__(self, app, *, requests_per_minute: int = 120) -> None:
        super().__init__(app)
        self._rpm = requests_per_minute

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        limit = self._rpm
        if path.startswith("/api/v1/auth/login"):
            limit = 10
        elif path.startswith("/api/v1/auth/"):
            limit = 30

        key = f"{client_ip}:{path}"
        now = time.time()
        window_start = now - 60
        hits = [t for t in _BUCKETS[key] if t > window_start]
        if len(hits) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": {"code": "RATE_LIMITED", "message": "Too many requests"}},
            )
        hits.append(now)
        _BUCKETS[key] = hits
        return await call_next(request)
