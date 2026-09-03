"""Authorization persistence, scoped to one merchant.

Same storage strategy as the other repositories: an in-memory store for
development and tests, the ``authorizations`` table behind the same interface
in deployment (``db/migrations/0003_policy_authorizations.sql``).

Two things this layer is responsible for, and only these two:

**Merchant scoping.** Every read and write takes ``merchant_id`` and filters on
it. A record belonging to another merchant is reported absent, never forbidden -
a 403 on a record that exists confirms the id is real to whoever is guessing.

**Atomic state transitions.** ``append_approval`` and ``mark_consumed`` return
``None`` when the record was not in the state they require. That is what makes
double-spend detectable rather than merely unlikely: the second concurrent call
finds nothing to transition instead of transitioning an already-spent record.

Deliberately no business rules here. Whether an approver is allowed to approve,
whether a quorum is met, whether a fingerprint matches - all of that lives in
the service. A repository that decided who may approve would be a second place
to change when the rule changes, and the two would drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

_AUTHORIZATIONS: dict[str, dict[str, Any]] = {}

#: Terminal states. A record in one of these never moves again.
TERMINAL = frozenset({"denied", "consumed", "expired", "revoked"})


def _now() -> datetime:
    return datetime.now(UTC)


def reset_authorizations() -> None:
    """Drop every authorization. For test isolation only."""
    _AUTHORIZATIONS.clear()


class AuthorizationRepository:
    """Authorization records for one merchant."""

    async def create(
        self,
        *,
        merchant_id: str,
        tenant_id: str | None,
        action_kind: str,
        amount_paise: int,
        currency: str,
        subject_id: str,
        action_fingerprint: str,
        requested_by: str,
        requested_by_role: str,
        status: str,
        required_approvals: int,
        policy_decision: dict[str, Any],
        risk: dict[str, Any],
        ttl_seconds: int,
        reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        auth_id = f"auth_{uuid.uuid4().hex[:16]}"
        now = _now()
        record = {
            "id": auth_id,
            "merchant_id": merchant_id,
            "tenant_id": tenant_id,
            "action_kind": action_kind,
            "amount_paise": amount_paise,
            "currency": currency,
            "subject_id": subject_id,
            "action_fingerprint": action_fingerprint,
            "requested_by": requested_by,
            "requested_by_role": requested_by_role,
            "status": status,
            "required_approvals": required_approvals,
            "approvers": [],
            "policy_decision": policy_decision,
            "risk": risk,
            "reasons": reasons or [],
            "created_at": now,
            # A denied record gets no life of its own: it is already terminal,
            # and giving it an expiry would imply it could have been used
            # before then.
            "expires_at": None if status == "denied" else now + timedelta(seconds=ttl_seconds),
            "approved_at": now if status == "approved" else None,
            "consumed_at": None,
        }
        _AUTHORIZATIONS[auth_id] = record
        return dict(record)

    async def get(self, auth_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        record = _AUTHORIZATIONS.get(auth_id)
        if record is None or record["merchant_id"] != merchant_id:
            return None
        return dict(record)

    async def list_for_merchant(
        self, *, merchant_id: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = [
            dict(r)
            for r in _AUTHORIZATIONS.values()
            if r["merchant_id"] == merchant_id and (status is None or r["status"] == status)
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    async def append_approval(
        self,
        auth_id: str,
        *,
        merchant_id: str,
        approver_id: str,
        approver_role: str,
    ) -> dict[str, Any] | None:
        """Record one approval, promoting to ``approved`` once the quorum is met.

        Returns ``None`` unless the record is currently pending and belongs to
        this merchant. The caller has already checked those things; checking
        again here is what closes the window between the check and the write.
        """
        record = _AUTHORIZATIONS.get(auth_id)
        if record is None or record["merchant_id"] != merchant_id:
            return None
        if record["status"] != "pending":
            return None

        record["approvers"].append(
            {"approver_id": approver_id, "role": approver_role, "at": _now()}
        )
        if len(record["approvers"]) >= record["required_approvals"]:
            record["status"] = "approved"
            record["approved_at"] = _now()
        return dict(record)

    async def mark_consumed(self, auth_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Spend the authorization, once.

        ``None`` on the second call is the single-use guarantee. Reading the
        status, deciding, and then writing would leave a gap two concurrent
        payments could both pass through; the read and the write happen here,
        together, with no await between them.
        """
        record = _AUTHORIZATIONS.get(auth_id)
        if record is None or record["merchant_id"] != merchant_id:
            return None
        if record["status"] != "approved":
            return None
        record["status"] = "consumed"
        record["consumed_at"] = _now()
        return dict(record)

    async def mark_expired(self, auth_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        record = _AUTHORIZATIONS.get(auth_id)
        if record is None or record["merchant_id"] != merchant_id:
            return None
        if record["status"] in TERMINAL:
            return dict(record)
        record["status"] = "expired"
        return dict(record)

    async def revoke(self, auth_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        record = _AUTHORIZATIONS.get(auth_id)
        if record is None or record["merchant_id"] != merchant_id:
            return None
        if record["status"] in TERMINAL:
            return None
        record["status"] = "revoked"
        return dict(record)


__all__ = ["TERMINAL", "AuthorizationRepository", "reset_authorizations"]
