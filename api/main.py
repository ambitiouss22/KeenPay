"""KeenPay API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config.settings import get_settings
from core.exceptions import KeenPayError
from core.logging import configure_logging, get_logger
from middleware.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    TenantContextMiddleware,
)
from middleware.security_headers import SecurityHeadersMiddleware
from routers.router import register_routers

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the engine's lifetime explicitly.

    Without a disposal step the pool's connections are closed by the garbage
    collector at interpreter shutdown, which under uvicorn's reload and in
    tests produces "Event loop is closed" noise and, worse, leaves sockets open
    against Postgres long enough to exhaust max_connections across restarts.
    """
    configure_logging()
    settings = get_settings()
    logger.info(
        "startup",
        env=settings.app_env,
        version=settings.app_version,
        routers=getattr(app.state, "mounted_routers", []),
    )
    try:
        yield
    finally:
        if not settings.use_in_memory_store:
            try:
                from database import get_engine

                await get_engine().dispose()
                logger.info("shutdown_engine_disposed")
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.warning("shutdown_engine_dispose_failed", error=str(exc))
        logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

    app = FastAPI(
        title="KeenPay API",
        version=settings.app_version,
        description="Agentic checkout with policy-gated payments",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_dev_routes else None,
        redoc_url="/redoc" if settings.enable_dev_routes else None,
    )

    # Starlette runs the LAST-added middleware outermost, so this list reads
    # inner-to-outer. The intended request order is:
    #   RequestID -> TenantContext -> RateLimit -> Logging -> route
    # TenantContext must precede RateLimit: the limiter is keyed per tenant and
    # needs identity resolved first.
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    def _rid(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @app.exception_handler(KeenPayError)
    async def keenpay_error_handler(request: Request, exc: KeenPayError) -> JSONResponse:
        # The status now comes from the exception class, not a guess here. The
        # old `403 if FORBIDDEN else 400` turned every not-found and conflict
        # into a 400.
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope(_rid(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Same envelope for FastAPI's own 422s.

        Untouched, these return {"detail": [...]} - a second error shape a
        client would have to special-case.
        """
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request failed validation",
                    "details": {"errors": jsonable_encoder(exc.errors())},
                    "request_id": _rid(request),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Anything unexpected becomes an opaque 500 carrying the request id.

        The message is deliberately generic. An unhandled exception's text can
        contain a connection string, a SQL fragment, a file path - the class of
        detail that helps an attacker and never helps a client. The full
        traceback goes to the log, keyed by the same request_id the caller is
        handed, so support can find it from the id alone.
        """
        request_id = _rid(request)
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred",
                    "request_id": request_id,
                }
            },
            # RequestIDMiddleware stamps this header after call_next returns,
            # which does not happen when the handler raised. Setting it here
            # keeps the id available on exactly the responses a caller most
            # needs to report.
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Keep one error envelope across the whole API.

        Routes raise HTTPException(detail={"error": {...}}). FastAPI's default
        handler returns that as {"detail": {"error": ...}} -- a second shape
        alongside the {"error": ...} KeenPayError already produces, so a client
        would have to parse two contracts. Unwrap it here rather than rewriting
        every raise site.
        """
        headers = getattr(exc, "headers", None)
        request_id = _rid(request)
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = dict(exc.detail)
            body["error"] = {**body["error"], "request_id": request_id}
            return JSONResponse(status_code=exc.status_code, content=body, headers=headers)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
                    "message": str(exc.detail),
                    "request_id": request_id,
                }
            },
            headers=headers,
        )

    report = register_routers(app, settings)
    app.state.mounted_routers = report.mounted
    if report.missing:
        # Expected while later phases are unwritten; noisy on purpose so a
        # genuinely absent router is visible rather than a silent 404.
        logger.info("routers_not_yet_present", modules=report.missing)

    return app


app = create_app()
