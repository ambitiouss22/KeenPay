"""Health and readiness probes."""

from fastapi import APIRouter

from config.settings import get_settings
from database import check_db
from dependencies.redis import check_redis
from schemas.common import HealthComponentStatus, HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


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
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict:
    pg = await check_db()
    redis_ok = await check_redis()
    ready = pg and redis_ok
    return {
        "status": "ready" if ready else "not_ready",
        "postgresql": "up" if pg else "down",
        "redis": "up" if redis_ok else "down",
    }
