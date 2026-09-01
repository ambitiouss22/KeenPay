"""Shared response helpers for controllers (routers)."""

from typing import Any

from fastapi.responses import JSONResponse


def error_response(
    *, code: str, message: str, status_code: int, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )
