"""Authentication request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    merchant_id: str = "merchant_keen"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: str
    merchant_id: str
    user_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RevokeRequest(BaseModel):
    refresh_token: str


class UserProfile(BaseModel):
    user_id: str
    email: str
    merchant_id: str
    role: str
    display_name: str | None = None
    last_login_at: datetime | None = None


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: Literal["service", "admin"] = "service"
    scopes: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=90, ge=1, le=365)


class ApiKeyCreateResponse(BaseModel):
    key_id: str
    api_key: str  # shown once
    name: str
    prefix: str
    role: str
    expires_at: datetime | None
