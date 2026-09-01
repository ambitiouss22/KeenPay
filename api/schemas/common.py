"""Shared Pydantic schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthComponentStatus(BaseModel):
    postgresql: str = "unknown"
    redis: str = "unknown"
    razorpay: str = "unknown"
    llm: str = "unknown"


class HealthResponse(BaseModel):
    status: str = "ok"
    degradation_level: int = Field(ge=0, le=3)
    components: HealthComponentStatus
    version: str
