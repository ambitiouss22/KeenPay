"""Idempotency key storage."""

from datetime import UTC, datetime, timedelta
from typing import Any

_KEYS: dict[str, dict[str, Any]] = {}
DEFAULT_TTL_SECONDS = 24 * 3600


def reset_idempotency() -> None:
    """Clear all keys. For test isolation."""
    _KEYS.clear()


class IdempotencyRepository:
    """In-memory idempotency store."""

    async def claim(
        self,
        merchant_id: str,
        endpoint: str,
        key: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """Claim a key. Returns None if new, else existing record."""
        scope = f"{merchant_id}:{endpoint}:{key}"

        if scope in _KEYS:
            record = _KEYS[scope]
            # Check expiry
            if record.get("expires_at") and datetime.now(UTC) > record["expires_at"]:
                # Expired - treat as new
                _KEYS[scope] = {
                    "state": "in_progress",
                    "fingerprint": fingerprint,
                    "expires_at": datetime.now(UTC) + timedelta(seconds=DEFAULT_TTL_SECONDS),
                }
                return None
            return record

        # New key
        _KEYS[scope] = {
            "state": "in_progress",
            "fingerprint": fingerprint,
            "expires_at": datetime.now(UTC) + timedelta(seconds=DEFAULT_TTL_SECONDS),
        }
        return None

    async def complete(
        self,
        merchant_id: str,
        endpoint: str,
        key: str,
        status_code: int,
        response_body: dict[str, Any],
    ) -> None:
        """Mark as completed."""
        scope = f"{merchant_id}:{endpoint}:{key}"
        if scope in _KEYS:
            _KEYS[scope]["state"] = "completed"
            _KEYS[scope]["status_code"] = status_code
            _KEYS[scope]["response_body"] = response_body
            _KEYS[scope]["completed_at"] = datetime.now(UTC)

    async def release(
        self,
        merchant_id: str,
        endpoint: str,
        key: str,
    ) -> None:
        """Release key only if still in_progress. Completed keys stay."""
        scope = f"{merchant_id}:{endpoint}:{key}"
        if scope in _KEYS and _KEYS[scope].get("state") == "in_progress":
            del _KEYS[scope]

    async def get_response(
        self,
        merchant_id: str,
        endpoint: str,
        key: str,
    ) -> tuple[int, dict[str, Any]] | None:
        """Get stored response for replay."""
        scope = f"{merchant_id}:{endpoint}:{key}"
        if scope in _KEYS:
            record = _KEYS[scope]
            if record.get("state") == "completed":
                return (record["status_code"], record.get("response_body", {}))
        return None
