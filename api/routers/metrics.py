"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Response

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    # Production: prometheus_client.generate_latest()
    body = "# HELP keenpay_up KeenPay API is running\nkeenpay_up 1\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")
