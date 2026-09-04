"""Claim-first idempotency.

The claim happens *before* the provider is called, not after. A key that is only
written on success leaves a window in which a retry arriving mid-flight reaches
Razorpay a second time, and the customer is charged twice. Claiming first turns
that race into a 409 instead of a double charge.
"""

import hashlib
import json
from enum import Enum
from typing import Any

from repositories.idempotency import IdempotencyRepository


class IdempotencyVerdict(Enum):
    """What the caller should do with this key."""

    CLAIMED = "claimed"  # New: proceed.
    IN_PROGRESS = "in_progress"  # Another request holds it: refuse.
    REPLAY = "replay"  # Seen and finished: return the stored response.
    CONFLICT = "conflict"  # Same key, different request body: refuse.


class IdempotencyService:
    """Claim-first idempotency over a scoped key store."""

    def __init__(self, repo: IdempotencyRepository | None = None):
        self._repo = repo or IdempotencyRepository()

    async def claim(
        self,
        merchant_id: str,
        endpoint: str,
        idempotency_key: str,
        request_body: dict[str, Any],
    ) -> IdempotencyVerdict:
        """Claim a key and report what the caller may do."""
        fingerprint = self._fingerprint(request_body)
        existing = await self._repo.claim(merchant_id, endpoint, idempotency_key, fingerprint)

        if existing is None:
            return IdempotencyVerdict.CLAIMED
        if existing.get("fingerprint") != fingerprint:
            # A different request reusing a spent key is a client bug at best.
            return IdempotencyVerdict.CONFLICT
        if existing.get("state") == "in_progress":
            return IdempotencyVerdict.IN_PROGRESS
        return IdempotencyVerdict.REPLAY

    async def complete(
        self,
        merchant_id: str,
        endpoint: str,
        idempotency_key: str,
        status_code: int,
        response_body: dict[str, Any],
    ) -> None:
        """Store the response this key will replay from now on."""
        await self._repo.complete(
            merchant_id,
            endpoint,
            idempotency_key,
            status_code,
            response_body,
        )

    async def release(self, merchant_id: str, endpoint: str, idempotency_key: str) -> None:
        """Release a key after a *pre-provider* failure only.

        Once the provider has been called the outcome is unknown, and freeing the
        key would invite the retry that claiming it was meant to stop.
        """
        await self._repo.release(merchant_id, endpoint, idempotency_key)

    async def replay_response(
        self,
        merchant_id: str,
        endpoint: str,
        idempotency_key: str,
    ) -> tuple[int, dict[str, Any]] | None:
        """Fetch the stored response for a completed key."""
        return await self._repo.get_response(merchant_id, endpoint, idempotency_key)

    @staticmethod
    def _fingerprint(request_body: dict[str, Any]) -> str:
        """Deterministic fingerprint of the request."""
        canonical = json.dumps(request_body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
