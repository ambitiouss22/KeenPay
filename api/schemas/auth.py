"""Authentication request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class AgentTokenRequest(BaseModel):
    """Ask for a short-lived credential for an AI agent.

    There is no ``merchant_id`` and no ``role``. Both are decided by the
    server: the merchant comes from the requesting operator's own token, and
    the role is always ``agent``. A body that could name either would let an
    operator mint a credential for another merchant, or an agent credential
    that could approve its own requests.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=16)
    ttl_seconds: int | None = Field(default=None, ge=60, le=3600)


class AgentTokenResponse(BaseModel):
    """The credential, plus exactly what it is allowed to do.

    ``scopes`` is echoed back as granted rather than as requested, so a caller
    can see what it actually received instead of assuming it got what it asked
    for.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    audience: str
    role: Literal["agent"] = "agent"
    scopes: list[str]
    merchant_id: str
    agent_id: str


class ApiKeyCreateResponse(BaseModel):
    key_id: str
    api_key: str  # shown once
    name: str
    prefix: str
    role: str
    expires_at: datetime | None
