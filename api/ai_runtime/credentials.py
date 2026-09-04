"""The agent credential: what it must say before the runtime will send it.

The Control Plane is the only party that can *verify* a token, because it is
the only party holding the signing key. Nothing here pretends otherwise. What
this module does is cheaper and still worth doing: it reads the token's own
claims and refuses to send one that is already, on its face, wrong for this
call - expired, aimed at another audience, or lacking the scope the tool needs.

Two reasons that is not security theatre.

*It fails closed at the right layer.* A tool that needs ``authorization:request``
and holds a catalogue-only token should stop before the network call, with a
message naming the missing scope, rather than after a 403 that says only
"forbidden" and leaves the operator guessing which of six things was wrong.

*It contains a leak.* A token minted for a different audience - another
service, another environment - is refused here even though the Control Plane
would also refuse it. Defence in depth costs one comparison.

The decode is unauthenticated by construction: base64 of the payload segment,
no signature check, no crypto library. Writing it out plainly is the honest
version. Using a JWT library here would suggest a verification that is not
happening, and would put a signing-capable dependency inside an image whose
whole point is that it cannot sign anything.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass, field
from typing import Any


class CredentialError(ValueError):
    """The credential is missing, malformed, expired, or wrongly scoped."""


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_claims(token: str) -> dict[str, Any]:
    """Read a JWT's payload without verifying it. See the module docstring."""
    parts = token.split(".")
    if len(parts) != 3:
        raise CredentialError("agent token is not a well-formed JWT")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CredentialError("agent token payload is not readable JSON") from exc
    if not isinstance(payload, dict):
        raise CredentialError("agent token payload is not an object")
    return payload


@dataclass(frozen=True)
class AgentCredential:
    """A parsed, not-yet-verified agent token plus the claims that gate its use."""

    token: str
    subject: str
    merchant_id: str
    role: str
    audience: tuple[str, ...] = ()
    scopes: frozenset[str] = frozenset()
    expires_at: int | None = None
    claims: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    #: Redacted on purpose. A credential ends up in log lines and exception
    #: reprs; the one place a bearer token must never appear is a log.
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"AgentCredential(subject={self.subject!r}, merchant_id={self.merchant_id!r}, "
            f"role={self.role!r}, scopes={sorted(self.scopes)!r}, token=<redacted>)"
        )

    @classmethod
    def parse(cls, token: str) -> AgentCredential:
        if not token or not token.strip():
            raise CredentialError("agent token is empty")
        claims = _decode_claims(token.strip())

        aud = claims.get("aud") or ()
        audience = (aud,) if isinstance(aud, str) else tuple(str(a) for a in aud)

        raw_scope = claims.get("scope") or claims.get("scopes") or ()
        if isinstance(raw_scope, str):
            scopes = frozenset(part for part in raw_scope.split() if part)
        else:
            scopes = frozenset(str(s) for s in raw_scope)

        exp = claims.get("exp")
        return cls(
            token=token.strip(),
            subject=str(claims.get("sub", "")),
            merchant_id=str(claims.get("merchant_id", "")),
            role=str(claims.get("role", "")),
            audience=audience,
            scopes=scopes,
            expires_at=int(exp) if isinstance(exp, (int, float)) else None,
            claims=claims,
        )

    def seconds_remaining(self, *, now: float | None = None) -> float | None:
        if self.expires_at is None:
            return None
        return self.expires_at - (now if now is not None else time.time())

    def is_expired(self, *, now: float | None = None, leeway_seconds: float = 0.0) -> bool:
        """Expired tokens are refused before the call, not after the 401.

        A token with no ``exp`` counts as expired. A credential that never dies
        is exactly what "short-lived" was meant to exclude, and treating a
        missing claim as "fine forever" is how that requirement quietly stops
        being true.
        """
        remaining = self.seconds_remaining(now=now)
        if remaining is None:
            return True
        return remaining <= leeway_seconds

    def check(
        self,
        *,
        audience: str,
        required_scopes: frozenset[str] | set[str] | tuple[str, ...] = (),
        now: float | None = None,
        leeway_seconds: float = 0.0,
    ) -> None:
        """Raise unless this credential may be used for this call."""
        if self.is_expired(now=now, leeway_seconds=leeway_seconds):
            raise CredentialError("agent token has expired or carries no expiry")

        if audience and audience not in self.audience:
            raise CredentialError(
                f"agent token audience {list(self.audience)!r} does not include {audience!r}"
            )

        missing = sorted(set(required_scopes) - self.scopes)
        if missing:
            raise CredentialError(f"agent token lacks required scope(s): {', '.join(missing)}")


__all__ = ["AgentCredential", "CredentialError"]
