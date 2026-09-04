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
from collections.abc import Sequence
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
    #: Present on tokens issued by the current version. Optional so tokens
    #: minted by an earlier one stay valid until they expire, rather than
    #: logging everyone out on deploy. Callers fall back to resolving the
    #: tenant from ``merchant_id`` — still token-derived, so still not
    #: spoofable.
    tenant_id: str | None = None
    jti: str | None = None
    token_type: str = "access"
    #: Present on agent credentials only. ``aud`` names the service the token
    #: may be presented to; ``scope`` narrows what it may do there. Both are
    #: optional, so ordinary user tokens are unaffected.
    aud: str | list[str] | None = None
    scope: str | None = None

    @property
    def audiences(self) -> tuple[str, ...]:
        if self.aud is None:
            return ()
        return (self.aud,) if isinstance(self.aud, str) else tuple(self.aud)

    @property
    def scopes(self) -> frozenset[str] | None:
        """``None`` means unscoped - the role alone decides. Never an empty set.

        The distinction matters. An empty scope list is a credential permitted
        to do nothing; ``None`` is one whose role is its only limit. Folding
        the two together would silently promote a deliberately-empty token into
        a fully-powered one.
        """
        if self.scope is None:
            return None
        return frozenset(part for part in self.scope.split() if part)


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
        audience: str | None = None,
        scopes: Sequence[str] | None = None,
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
        if audience is not None:
            payload["aud"] = audience
        if scopes is not None:
            # Space-separated, as OAuth 2 writes it. Sorted so two tokens
            # granting the same access are byte-identical, which makes them
            # comparable in a log or a test without normalising first.
            payload["scope"] = " ".join(sorted(set(scopes)))

        if extra_claims:
            # Extras go underneath, then the trusted claims are written back
            # over the top. The other order would let a caller passing
            # extra_claims={"role": "admin"} or {"tenant_id": ...} silently
            # mint a token for someone else.
            payload = {**extra_claims, **payload}

        return jwt.encode(
            payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm
        )

    def decode_access_token(self, token: str, *, audience: str | None = None) -> TokenClaims:
        """Verify signature and expiry, and return the claims.

        ``algorithms`` is pinned to the configured algorithm. Passing the
        server's own list rather than trusting the token's header is what stops
        algorithm confusion: without it an attacker can re-sign a token as
        ``none``, or as HMAC using a public RSA key as the secret, and have it
        accepted.

        Audience is checked here rather than left to the JWT library. The
        library's default is to reject any token carrying an ``aud`` claim when
        the caller did not name an expected audience - which would have made
        agent credentials undecodable on every ordinary route. Doing it
        explicitly keeps unscoped user tokens working and still refuses an
        agent token presented to a service it was not minted for.
        """
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
                options={"verify_aud": False},
            )
        except ExpiredSignatureError as exc:
            raise TokenExpiredError("token expired") from exc
        except JWTError as exc:
            raise TokenError("invalid token") from exc

        if audience is not None:
            token_aud = payload.get("aud")
            allowed = (
                ()
                if token_aud is None
                else ((token_aud,) if isinstance(token_aud, str) else tuple(token_aud))
            )
            if audience not in allowed:
                raise TokenError("invalid audience")

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
