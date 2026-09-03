"""Health and readiness probes.

Three endpoints answering three different questions, which is why they are not
one endpoint:

``/health/live``
    Is the process alive? Nothing but the process itself. A liveness probe that
    checks the database restarts the app during a database outage - turning a
    dependency blip into an outage of your own.

``/health/ready``
    Should this instance receive traffic? Checks the dependencies a request
    actually needs, and **answers 503 when it should not**. A readiness probe
    that always returns 200 tells the load balancer everything is fine while
    every request fails.

``/health``
    Human-facing detail: per-component status and a degradation level.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from config.settings import get_settings
from core.logging import get_logger
from database import check_db, get_session_factory
from dependencies.redis import check_redis
from schemas.common import HealthComponentStatus, HealthResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["health"])


async def check_rls() -> tuple[bool, str]:
    """Verify tenant isolation is actually switched on in this database.

    Readiness is not only "can I reach Postgres" - it is "is Postgres in a
    state where serving traffic is safe". A database that answers queries but
    has row-level security disabled, or where the app role can bypass it, would
    serve every tenant's rows to every caller. Refusing traffic is the right
    answer; a silent cross-tenant leak is the wrong one.

    Returns (ok, detail).
    """
    settings = get_settings()
    if settings.use_in_memory_store:
        return True, "skipped (in-memory store)"

    try:
        factory = get_session_factory()
        async with factory() as session:
            unprotected = await session.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public'
                       AND c.relkind = 'r'
                       AND NOT c.relrowsecurity
                       AND EXISTS (
                           SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = c.relname
                              AND column_name = 'tenant_id'
                       )
                    """
                )
            )
            can_bypass = await session.scalar(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        logger.warning("rls_check_failed", error=str(exc))
        return False, f"unverified: {type(exc).__name__}"

    if unprotected:
        return False, f"{unprotected} tenant table(s) without row-level security"
    if can_bypass:
        return False, "the connected role can bypass row-level security"
    return True, "enforced"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    pg = "up" if await check_db() else "down"
    redis_status = "up" if await check_redis() else "down"
    razorpay = "up" if settings.razorpay_mock or settings.razorpay_key_id else "degraded"
    llm = "up" if settings.openai_api_key else "degraded"

    components = HealthComponentStatus(
        postgresql=pg,
        redis=redis_status,
        razorpay=razorpay,
        llm=llm,
    )
    statuses = [pg, redis_status, razorpay, llm]
    degradation = 0
    if any(s == "down" for s in statuses):
        degradation = 2
    elif any(s == "degraded" for s in statuses):
        degradation = 1

    return HealthResponse(
        status="ok" if degradation < 2 else "degraded",
        degradation_level=degradation,
        components=components,
        version=settings.app_version,
    )


@router.get("/health/live")
async def liveness() -> dict:
    """Alive, deliberately without touching any dependency."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(response: Response) -> dict:
    """Ready to serve traffic, and honest about it in the status code."""
    settings = get_settings()
    pg = await check_db()
    redis_ok = await check_redis()
    rls_ok, rls_detail = await check_rls()

    # Redis backs trace streaming, not the money path, so it degrades rather
    # than blocks. Postgres and RLS are non-negotiable: without either, a
    # request is a wrong answer rather than a slow one.
    ready = pg and rls_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning(
            "not_ready", postgresql=pg, redis=redis_ok, rls=rls_ok, rls_detail=rls_detail
        )

    return {
        "status": "ready" if ready else "not_ready",
        "postgresql": "up" if pg else "down",
        "redis": "up" if redis_ok else "down",
        "rls": rls_detail,
        "version": settings.app_version,
    }
