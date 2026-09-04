"""Authentication service — login, refresh, API keys."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from config.settings import Settings, get_settings
from core.hashing import (
    generate_api_key,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from core.jwt import JWTManager

logger = structlog.get_logger(__name__)


def _as_str(value: object) -> str | None:
    """Normalise a tenant id to a string, tolerating UUID or None."""
    return None if value is None else str(value)


@dataclass
class AuthenticatedPrincipal:
    """Who the caller is, established from verified credentials only.

    ``tenant_id`` is the value row-level security is pinned to. It is optional
    because tokens issued by an earlier version do not carry the claim; callers
    fall back to resolving it from ``merchant_id``, which is equally
    token-derived and so equally unspoofable.
    """

    user_id: str
    merchant_id: str
    role: str
    auth_method: str  # "jwt" | "api_key"
    api_key_id: str | None = None
    tenant_id: str | None = None
    #: Set only for a scoped credential. ``None`` means the role alone decides
    #: what the caller may do; a set narrows it. Never widens it - see
    #: ``core.rbac.effective_permissions``.
    scopes: frozenset[str] | None = None
    #: The audience the presented token named, when it named one.
    audience: str | None = None


class AuthService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._jwt = JWTManager(self._settings)

    async def authenticate_password(
        self,
        *,
        email: str,
        password: str,
        merchant_id: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, str, AuthenticatedPrincipal]:
        from repositories.users import UserRepository

        repo = UserRepository()
        user = await repo.get_by_email(email=email, merchant_id=merchant_id)
        if user is None or not user.get("active"):
            await repo.log_auth_event(
                "login_failed", metadata={"email": email, "reason": "unknown_user"}
            )
            raise ValueError("invalid credentials")

        if user.get("locked_until") and user["locked_until"] > datetime.now(UTC):
            raise ValueError("account locked")

        if not user.get("password_hash") or not verify_password(password, user["password_hash"]):
            await repo.record_failed_login(user["id"])
            await repo.log_auth_event(
                "login_failed", user_id=user["id"], metadata={"reason": "bad_password"}
            )
            raise ValueError("invalid credentials")

        await repo.clear_failed_logins(user["id"])

        # The plaintext only exists here, so this is the one moment a legacy
        # bcrypt hash can be upgraded to Argon2id. Failure to persist must not
        # fail the login — the user is already authenticated, and the old hash
        # still works; it just gets retried on their next sign-in.
        if needs_rehash(user["password_hash"]):
            try:
                await repo.update_password_hash(user["id"], hash_password(password))
            except Exception:  # noqa: BLE001 - upgrade is best-effort
                logger.warning("password_rehash_failed", user_id=user["id"])

        principal = AuthenticatedPrincipal(
            user_id=user["id"],
            merchant_id=user["merchant_id"],
            role=user["role"],
            auth_method="jwt",
            tenant_id=_as_str(user.get("tenant_id")),
        )
        access, refresh = await self._issue_token_pair(
            principal, user_agent=user_agent, ip_address=ip_address
        )
        await repo.log_auth_event(
            "login_success", user_id=user["id"], ip_address=ip_address, user_agent=user_agent
        )
        return access, refresh, principal

    async def refresh_tokens(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, str, AuthenticatedPrincipal]:
        from repositories.users import UserRepository

        repo = UserRepository()
        token_hash = hash_token(refresh_token)
        record = await repo.get_refresh_token(token_hash)
        if record is None or record.get("revoked_at"):
            raise ValueError("invalid refresh token")
        if record["expires_at"] < datetime.now(UTC):
            raise ValueError("refresh token expired")

        user = await repo.get_by_id(record["user_id"])
        if user is None or not user.get("active"):
            raise ValueError("user inactive")

        await repo.revoke_refresh_token(record["id"], reason="rotated")
        principal = AuthenticatedPrincipal(
            user_id=user["id"],
            merchant_id=user["merchant_id"],
            role=user["role"],
            auth_method="jwt",
            tenant_id=_as_str(user.get("tenant_id")),
        )
        access, new_refresh = await self._issue_token_pair(
            principal,
            family_id=record["family_id"],
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await repo.log_auth_event("token_refreshed", user_id=user["id"])
        return access, new_refresh, principal

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        from repositories.users import UserRepository

        repo = UserRepository()
        token_hash = hash_token(refresh_token)
        record = await repo.get_refresh_token(token_hash)
        if record:
            await repo.revoke_refresh_token(record["id"], reason="logout")
            await repo.log_auth_event("token_revoked", user_id=record["user_id"])

    async def authenticate_api_key(self, raw_key: str) -> AuthenticatedPrincipal:
        from repositories.users import UserRepository

        if not raw_key.startswith("kp_"):
            raise ValueError("invalid api key format")

        repo = UserRepository()
        prefix = raw_key[:12]
        key_hash = hash_token(raw_key)
        record = await repo.get_api_key_by_prefix(prefix)
        if record is None or record["key_hash"] != key_hash:
            raise ValueError("invalid api key")
        if not record.get("active") or record.get("revoked_at"):
            raise ValueError("api key revoked")
        if record.get("expires_at") and record["expires_at"] < datetime.now(UTC):
            raise ValueError("api key expired")

        await repo.touch_api_key(record["id"])
        await repo.log_auth_event(
            "api_key_used", api_key_id=record["id"], merchant_id=record["merchant_id"]
        )
        return AuthenticatedPrincipal(
            user_id=f"apikey:{record['id']}",
            merchant_id=record["merchant_id"],
            role=record["role"],
            auth_method="api_key",
            api_key_id=record["id"],
            tenant_id=_as_str(record.get("tenant_id")),
        )

    async def create_api_key(
        self,
        *,
        name: str,
        merchant_id: str,
        role: str,
        created_by: str,
        scopes: list[str] | None = None,
        expires_in_days: int | None = 90,
    ) -> tuple[str, dict]:
        from repositories.users import UserRepository

        raw, prefix, key_hash = generate_api_key()
        expires_at = None
        if expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        repo = UserRepository()
        record = await repo.create_api_key(
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            merchant_id=merchant_id,
            role=role,
            scopes=scopes or [],
            expires_at=expires_at,
            created_by=created_by,
        )
        return raw, record

    def verify_access_token(self, token: str) -> AuthenticatedPrincipal:
        claims = self._jwt.decode_access_token(token)
        if claims.audiences and self._settings.control_plane_audience not in claims.audiences:
            # A token minted for another service must not be replayable here,
            # even when it is correctly signed. Tokens with no audience are
            # ordinary user tokens and stay unaffected.
            raise ValueError("token was not issued for this service")
        return AuthenticatedPrincipal(
            user_id=claims.sub,
            merchant_id=claims.merchant_id,
            role=claims.role,
            auth_method="jwt",
            tenant_id=claims.tenant_id,
            scopes=claims.scopes,
            audience=claims.audiences[0] if claims.audiences else None,
        )

    def issue_agent_token(
        self,
        *,
        agent_id: str,
        merchant_id: str,
        scopes: list[str],
        issued_by: str,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[str, int, list[str]]:
        """Mint a short-lived, audience-restricted credential for an AI agent.

        Three properties, and each exists because of a specific failure it
        prevents.

        *Short-lived.* The default life is minutes, not the hour an interactive
        session gets. An agent credential lives in a separate container whose
        whole job is to process untrusted text; the window in which a leaked one
        is useful should be small enough that noticing the leak still helps.

        *Audience-restricted.* It names this service, so it cannot be replayed
        against another one that happens to trust the same signing key.

        *Least privilege.* The role is ``agent``, which cannot approve, refund
        or capture anything, and the scopes narrow it further. A scope outside
        :data:`AGENT_GRANTABLE_SCOPES` is refused rather than dropped, so an
        operator asking for something impossible is told, not quietly given
        less than they asked for.
        """
        from core.rbac import AGENT_GRANTABLE_SCOPES, Role

        requested = [s.strip() for s in scopes if s and s.strip()]
        if not requested:
            raise ValueError("at least one scope is required")

        unknown = sorted(set(requested) - AGENT_GRANTABLE_SCOPES)
        if unknown:
            raise ValueError(f"scope(s) not grantable to an agent: {', '.join(unknown)}")

        ttl = ttl_seconds or self._settings.agent_token_ttl_seconds
        if ttl < 60 or ttl > self._settings.agent_token_max_ttl_seconds:
            raise ValueError(
                f"ttl_seconds must be between 60 and "
                f"{self._settings.agent_token_max_ttl_seconds}"
            )

        granted = sorted(set(requested))
        token = self._jwt.create_access_token(
            user_id=agent_id,
            merchant_id=merchant_id,
            role=Role.AGENT.value,
            tenant_id=tenant_id,
            expires_delta=timedelta(seconds=ttl),
            audience=self._settings.control_plane_audience,
            scopes=granted,
        )
        logger.info(
            "agent_token_issued",
            agent_id=agent_id,
            merchant_id=merchant_id,
            issued_by=issued_by,
            scopes=granted,
            ttl_seconds=ttl,
        )
        return token, ttl, granted

    async def _issue_token_pair(
        self,
        principal: AuthenticatedPrincipal,
        *,
        family_id: str | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, str]:
        from repositories.users import UserRepository

        access = self._jwt.create_access_token(
            user_id=principal.user_id,
            merchant_id=principal.merchant_id,
            role=principal.role,
            tenant_id=principal.tenant_id,
        )
        refresh_raw = self._jwt.create_refresh_token_value()
        refresh_hash = hash_token(refresh_raw)
        expires_at = datetime.now(UTC) + timedelta(days=self._settings.jwt_refresh_expire_days)

        repo = UserRepository()
        await repo.store_refresh_token(
            user_id=principal.user_id,
            token_hash=refresh_hash,
            family_id=family_id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await repo.log_auth_event("token_issued", user_id=principal.user_id)
        return access, refresh_raw
