"""JWT access token creation and validation."""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from pydantic import BaseModel, Field

from config.settings import Settings, get_settings


class TokenClaims(BaseModel):
    sub: str
    merchant_id: str
    role: str
    exp: int
    iat: int
    jti: str | None = None
    token_type: str = "access"


class JWTManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def create_access_token(
        self,
        *,
        user_id: str,
        merchant_id: str,
        role: str,
        expires_delta: timedelta | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(UTC)
        delta = expires_delta or timedelta(minutes=self._settings.jwt_access_expire_minutes)
        payload: dict[str, Any] = {
            "sub": user_id,
            "merchant_id": merchant_id,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + delta).timestamp()),
            "token_type": "access",
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm)

    def decode_access_token(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
            )
            if payload.get("token_type") != "access":
                raise JWTError("invalid token type")
            return TokenClaims(**payload)
        except JWTError as exc:
            raise ValueError("invalid or expired token") from exc

    def create_refresh_token_value(self) -> str:
        import secrets

        return secrets.token_urlsafe(48)
