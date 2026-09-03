"""JWT access token creation and validation.

The access token is the only place tenant identity is allowed to come from.
Everything downstream — the row-level-security pin, the rate-limit bucket,
authorization checks — reads ``tenant_id`` from a verified token and never from
a header, query parameter or body field. A client that could name its own
tenant could read any tenant's data, so there is deliberately no code path that
lets it.

Refresh tokens are not JWTs. They are opaque random strings stored as hashes,
which makes them revocable: a stolen refresh token can be killed server-side,
where a self-contained JWT would stay valid until it expired.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel, ValidationError

from config.settings import Settings, get_settings


class TokenError(ValueError):
    """Token could not be verified."""


class TokenExpiredError(TokenError):
    """Token was well-formed and correctly signed, but past its expiry.

    Separate from :class:`TokenError` so the API can tell a client "refresh"
    rather than "your token is bad", without a caller having to match on
    message text.
    """


class TokenClaims(BaseModel):
    sub: str
    merchant_id: str
    role: str
    exp: int
    iat: int
    #: Present on tokens issued from Phase 2 onward. Optional so tokens minted
    #: by the previous version stay valid until they expire, rather than
    #: logging everyone out on deploy. Callers fall back to resolving the
    #: tenant from ``merchant_id`` — still token-derived, so still not
    #: spoofable.
    tenant_id: str | None = None
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
        tenant_id: str | uuid.UUID | None = None,
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
            "jti": secrets.token_urlsafe(16),
            "token_type": "access",
        }
        if tenant_id is not None:
            payload["tenant_id"] = str(tenant_id)

        if extra_claims:
            # Extras go underneath, then the trusted claims are written back
            # over the top. The other order would let a caller passing
            # extra_claims={"role": "admin"} or {"tenant_id": ...} silently
            # mint a token for someone else.
            payload = {**extra_claims, **payload}

        return jwt.encode(
            payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm
        )

    def decode_access_token(self, token: str) -> TokenClaims:
        """Verify signature and expiry, and return the claims.

        ``algorithms`` is pinned to the configured algorithm. Passing the
        server's own list rather than trusting the token's header is what stops
        algorithm confusion: without it an attacker can re-sign a token as
        ``none``, or as HMAC using a public RSA key as the secret, and have it
        accepted.
        """
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
            )
        except ExpiredSignatureError as exc:
            raise TokenExpiredError("token expired") from exc
        except JWTError as exc:
            raise TokenError("invalid token") from exc

        if payload.get("token_type") != "access":
            # A refresh token presented as a bearer credential must not work.
            raise TokenError("invalid token type")

        try:
            return TokenClaims(**payload)
        except ValidationError as exc:
            raise TokenError("malformed token claims") from exc

    def create_refresh_token_value(self) -> str:
        """A high-entropy opaque string. Stored hashed; never a JWT."""
        return secrets.token_urlsafe(48)


__all__ = [
    "JWTManager",
    "TokenClaims",
    "TokenError",
    "TokenExpiredError",
]
