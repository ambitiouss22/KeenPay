"""KeenPay API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.logging import configure_logging
from config.settings import get_settings
from core.exceptions import KeenPayError
from middleware.rate_limit import RateLimitMiddleware
from middleware.request_id import RequestIDMiddleware
from middleware.security_headers import SecurityHeadersMiddleware
from routers import admin, auth, catalog, health, orders, sessions, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield


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

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    @app.exception_handler(KeenPayError)
    async def keenpay_error_handler(_request: Request, exc: KeenPayError) -> JSONResponse:
        status_code = 403 if exc.code == "FORBIDDEN" else 400
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(sessions.router)
    app.include_router(orders.router)
    app.include_router(admin.router)
    app.include_router(webhooks.router)

    if settings.enable_metrics:
        from routers.metrics import router as metrics_router

        app.include_router(metrics_router)

    if settings.enable_dev_routes:
        from routers.dev import router as dev_router

        app.include_router(dev_router)

    from ws.session import router as ws_router

    app.include_router(ws_router)

    return app


app = create_app()
