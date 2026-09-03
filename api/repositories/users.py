"""User and auth persistence.

Production: SQLAlchemy/asyncpg queries against users, refresh_tokens, api_keys.
Dev/test: in-memory store when DATABASE_URL contains 'test' or no DB available.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from core.hashing import hash_password

_DEV_PASSWORD_HASH = hash_password("KeenPayDev1!")

# In-memory dev store (replaced by real DB queries in production wiring)
_DEV_USERS: dict[str, dict[str, Any]] = {
    "user_dev_shopper": {
        "id": "user_dev_shopper",
        "email": "shopper@keenpay.dev",
        "password_hash": _DEV_PASSWORD_HASH,
        "merchant_id": "merchant_keen",
        "role": "shopper",
        "display_name": "Dev Shopper",
        "active": True,
        "locked_until": None,
        "failed_login_count": 0,
        "last_login_at": None,
        # Real rows carry a tenant UUID (Phase 1). The dev store has no
        # database to read one from, so it stays None and callers resolve the
        # tenant from merchant_id instead.
        "tenant_id": None,
    },
    "user_dev_support": {
        "id": "user_dev_support",
        "email": "support@keenpay.dev",
        "password_hash": _DEV_PASSWORD_HASH,
        "merchant_id": "merchant_keen",
        "role": "support_agent",
        "display_name": "Dev Support",
        "active": True,
        "locked_until": None,
        "failed_login_count": 0,
        "last_login_at": None,
        # Real rows carry a tenant UUID (Phase 1). The dev store has no
        # database to read one from, so it stays None and callers resolve the
        # tenant from merchant_id instead.
        "tenant_id": None,
    },
    "user_dev_manager": {
        "id": "user_dev_manager",
        "email": "manager@keenpay.dev",
        "password_hash": _DEV_PASSWORD_HASH,
        "merchant_id": "merchant_keen",
        "role": "manager",
        "display_name": "Dev Manager",
        "active": True,
        "locked_until": None,
        "failed_login_count": 0,
        "last_login_at": None,
        # Real rows carry a tenant UUID (Phase 1). The dev store has no
        # database to read one from, so it stays None and callers resolve the
        # tenant from merchant_id instead.
        "tenant_id": None,
    },
    "user_dev_admin": {
        "id": "user_dev_admin",
        "email": "admin@keenpay.dev",
        "password_hash": _DEV_PASSWORD_HASH,
        "merchant_id": "merchant_keen",
        "role": "admin",
        "display_name": "Dev Admin",
        "active": True,
        "locked_until": None,
        "failed_login_count": 0,
        "last_login_at": None,
        # Real rows carry a tenant UUID (Phase 1). The dev store has no
        # database to read one from, so it stays None and callers resolve the
        # tenant from merchant_id instead.
        "tenant_id": None,
    },
}

_DEV_EMAIL_INDEX = {f"{u['merchant_id']}:{u['email']}": u for u in _DEV_USERS.values()}
_REFRESH_TOKENS: dict[str, dict[str, Any]] = {}
_API_KEYS: dict[str, dict[str, Any]] = {}
_AUTH_AUDIT: list[dict[str, Any]] = []


class UserRepository:
    async def get_by_email(self, *, email: str, merchant_id: str) -> dict[str, Any] | None:
        return _DEV_EMAIL_INDEX.get(f"{merchant_id}:{email}")

    async def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        return _DEV_USERS.get(user_id)

    async def update_password_hash(self, user_id: str, new_hash: str) -> None:
        """Replace a stored hash. Used to migrate legacy hashes on login."""
        user = _DEV_USERS.get(user_id)
        if user:
            user["password_hash"] = new_hash

    async def record_failed_login(self, user_id: str) -> None:
        user = _DEV_USERS.get(user_id)
        if not user:
            return
        user["failed_login_count"] = user.get("failed_login_count", 0) + 1
        if user["failed_login_count"] >= 5:
            user["locked_until"] = datetime.now(UTC) + timedelta(minutes=15)

    async def clear_failed_logins(self, user_id: str) -> None:
        user = _DEV_USERS.get(user_id)
        if user:
            user["failed_login_count"] = 0
            user["locked_until"] = None
            user["last_login_at"] = datetime.now(UTC)

    async def store_refresh_token(
        self,
        *,
        user_id: str,
        token_hash: str,
        family_id: str | None,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "user_id": user_id,
            "token_hash": token_hash,
            "family_id": family_id or str(uuid4()),
            "expires_at": expires_at,
            "revoked_at": None,
            "user_agent": user_agent,
            "ip_address": ip_address,
        }
        _REFRESH_TOKENS[token_hash] = record
        return record

    async def get_refresh_token(self, token_hash: str) -> dict[str, Any] | None:
        return _REFRESH_TOKENS.get(token_hash)

    async def revoke_refresh_token(self, token_id: str, *, reason: str) -> None:
        for record in _REFRESH_TOKENS.values():
            if record["id"] == token_id:
                record["revoked_at"] = datetime.now(UTC)
                record["revoke_reason"] = reason

    async def create_api_key(
        self,
        *,
        name: str,
        prefix: str,
        key_hash: str,
        merchant_id: str,
        role: str,
        scopes: list[str],
        expires_at: datetime | None,
        created_by: str,
    ) -> dict[str, Any]:
        key_id = f"key_{uuid4().hex[:16]}"
        record = {
            "id": key_id,
            "name": name,
            "key_prefix": prefix,
            "key_hash": key_hash,
            "merchant_id": merchant_id,
            "role": role,
            "scopes": scopes,
            "active": True,
            "expires_at": expires_at,
            "created_by": created_by,
            "revoked_at": None,
        }
        _API_KEYS[prefix] = record
        return record

    async def get_api_key_by_prefix(self, prefix: str) -> dict[str, Any] | None:
        return _API_KEYS.get(prefix)

    async def touch_api_key(self, key_id: str) -> None:
        for record in _API_KEYS.values():
            if record["id"] == key_id:
                record["last_used_at"] = datetime.now(UTC)

    async def log_auth_event(
        self,
        event_type: str,
        *,
        user_id: str | None = None,
        api_key_id: str | None = None,
        merchant_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        _AUTH_AUDIT.append(
            {
                "event_type": event_type,
                "user_id": user_id,
                "api_key_id": api_key_id,
                "merchant_id": merchant_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "metadata": metadata or {},
                "created_at": datetime.now(UTC),
            }
        )
