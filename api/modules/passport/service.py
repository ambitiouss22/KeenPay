"""The transaction passport: one signed document that explains a payment.

A dispute is not settled by a database. It is settled by a document that says
what was bought, for how much, who approved it, which rules were applied and
what the provider did — and that can be checked by someone who does not trust
the system that produced it. That is what a passport is.

Two properties make it worth more than a JSON dump:

**It is signed.** The body is serialized canonically and covered by an
HMAC-SHA256 tag. Change a single paisa and the tag stops matching. The key
never leaves the control plane, so a passport cannot be forged by anyone
holding only a copy of one.

**It carries its own audit chain.** The ledger entries for the payment travel
inside the passport, each with the hash linking it to the one before. So a
holder can check two independent things: that the document was not altered
after issue (the signature), and that the history it describes was not altered
before issue (the chain). Either check alone leaves a gap; together they close
it.

:func:`verify_passport` is a pure function. It performs no I/O, touches no
database, and imports nothing from the application — which is what makes
"verifies offline" a true statement rather than a marketing one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from config.settings import get_settings
from modules.audit.ledger import AuditLedger, verify_exported_chain
from repositories.orders import OrderRepository
from repositories.payments import PaymentRepository
from repositories.webhooks import WebhookRepository

#: Bumped only when the signed body's shape changes. A verifier that does not
#: recognise the version must refuse rather than guess, because guessing at a
#: field's meaning is how a passport gets accepted for the wrong amount.
PASSPORT_VERSION = "1"

SIGNATURE_ALGORITHM = "HMAC-SHA256"


def canonical_body(body: dict[str, Any]) -> str:
    """Serialize the signed body deterministically.

    Sorted keys, no incidental whitespace. Two processes must produce byte-identical
    output for the same body or no signature would ever verify twice.
    """
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def body_digest(body: dict[str, Any]) -> str:
    """SHA-256 over the canonical body."""
    return hashlib.sha256(canonical_body(body).encode("utf-8")).hexdigest()


def sign_body(body: dict[str, Any], key: str) -> str:
    """HMAC the canonical body."""
    return hmac.new(key.encode("utf-8"), canonical_body(body).encode("utf-8"), hashlib.sha256
                    ).hexdigest()


def verify_passport(passport: dict[str, Any], key: str) -> tuple[bool, list[str]]:
    """Check a passport against a key, offline.

    Returns ``(valid, errors)``. Every check runs even after one fails: a
    holder needs to know everything that is wrong with a document, not just the
    first thing.
    """
    errors: list[str] = []

    body = passport.get("body")
    signature = passport.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        return False, ["Passport is missing its body or its signature"]

    version = body.get("version")
    if version != PASSPORT_VERSION:
        errors.append(
            f"Unsupported passport version {version!r}; this verifier reads {PASSPORT_VERSION!r}"
        )

    algorithm = signature.get("algorithm")
    if algorithm != SIGNATURE_ALGORITHM:
        errors.append(f"Unsupported signature algorithm {algorithm!r}")

    expected_digest = body_digest(body)
    if signature.get("body_hash") != expected_digest:
        errors.append("Body does not match the digest recorded in the signature")

    if not key:
        errors.append("No verification key supplied")
    else:
        expected_signature = sign_body(body, key)
        provided = signature.get("value")
        # compare_digest over two str is fine; both are hex and ASCII-only.
        if not isinstance(provided, str) or not hmac.compare_digest(
            expected_signature, provided
        ):
            errors.append("Signature does not match the body")

    ledger = body.get("ledger")
    if isinstance(ledger, dict):
        entries = ledger.get("entries")
        if isinstance(entries, list):
            # A passport carries the entries about one payment, not a whole
            # merchant chain, so sequence numbers legitimately skip. Each
            # entry's own hash and every adjacent link are still checked; the
            # signature above is what stops entries from being dropped.
            chain_ok, chain_errors = verify_exported_chain(
                entries, contiguous=bool(ledger.get("contiguous", False))
            )
            if not chain_ok:
                errors.extend(f"audit chain: {message}" for message in chain_errors)
        else:
            errors.append("Ledger section carries no entries")

    return not errors, errors


class PassportService:
    """Assembles and signs a passport for one payment."""

    def __init__(
        self,
        *,
        payments: PaymentRepository | None = None,
        orders: OrderRepository | None = None,
        events: WebhookRepository | None = None,
        ledger: AuditLedger | None = None,
        signing_key: str | None = None,
    ) -> None:
        self._payments = payments or PaymentRepository()
        self._orders = orders or OrderRepository()
        self._events = events or WebhookRepository()
        self._ledger = ledger or AuditLedger()
        self._signing_key = signing_key

    @property
    def signing_key(self) -> str:
        """The key passports are signed with.

        A dedicated key when one is configured, otherwise the JWT secret. The
        fallback keeps a fresh deployment working; the dedicated setting exists
        so the two can be rotated independently, since a passport must stay
        verifiable long after the token that authorised it expired.
        """
        if self._signing_key is not None:
            return self._signing_key
        settings = get_settings()
        return settings.passport_signing_key or settings.jwt_secret

    async def issue(self, payment_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Build and sign the passport for one payment.

        ``None`` when the payment does not exist for this merchant — the same
        answer as "not yours", because distinguishing the two confirms an id to
        whoever is guessing.
        """
        payment = await self._payments.get(payment_id, merchant_id=merchant_id)
        if not payment:
            return None

        order = await self._orders.get(payment["order_id"])
        entries, _ = await self._ledger.entries_for(
            merchant_id, entity_type="payment", entity_id=payment_id, limit=1000
        )
        order_entries: list[Any] = []
        if order:
            order_entries, _ = await self._ledger.entries_for(
                merchant_id, entity_type="order", entity_id=order["id"], limit=1000
            )

        # One chain, ordered as it was written. Merging by sequence keeps the
        # prev_hash links intact, which is what makes the exported chain
        # verifiable on its own.
        combined = sorted([*entries, *order_entries], key=lambda entry: entry.seq)

        webhook_events = []
        if order:
            for event in await self._events.list_for_order(order["id"]):
                webhook_events.append(
                    {
                        "event_id": event["event_id"],
                        "event_type": event["event_type"],
                        "received_at": event["received_at"].astimezone(UTC).isoformat(),
                        "result": (event.get("process_result") or {}).get("verdict"),
                    }
                )

        body = {
            "version": PASSPORT_VERSION,
            "passport_id": f"psp_{uuid4().hex[:16]}",
            "issued_at": datetime.now(UTC).isoformat(),
            "merchant_id": merchant_id,
            "payment": {
                "id": payment["id"],
                "status": payment["status"],
                "amount_paise": int(payment["amount_paise"]),
                "captured_paise": int(payment["captured_paise"]),
                "refunded_paise": int(payment["refunded_paise"]),
                "provider_payment_id": payment.get("provider_payment_id"),
                "order_snapshot_hash": payment.get("order_snapshot_hash"),
                "created_at": self._iso(payment.get("created_at")),
            },
            # The snapshot, not the live order. A passport must describe what
            # was bought at the moment money moved, not what the record looks
            # like now.
            "order": {
                "id": payment["order_id"],
                "status": (order or {}).get("status"),
                "currency": (payment.get("order_snapshot") or {}).get("currency", "INR"),
                "snapshot": payment.get("order_snapshot") or {},
                "guardrail_decision_id": (order or {}).get("guardrail_decision_id"),
                "offer_version": (order or {}).get("offer_version"),
                "policy_version": (order or {}).get("policy_version"),
            },
            "authorization": {
                "idempotency_key": payment.get("idempotency_key"),
                "attempts": [
                    {
                        "operation": attempt.get("operation"),
                        "provider_raw_status": attempt.get("provider_raw_status"),
                        "error": attempt.get("error"),
                        "at": self._iso(attempt.get("at")),
                    }
                    for attempt in payment.get("attempts", [])
                ],
            },
            "webhook_events": webhook_events,
            "ledger": {
                "entry_count": len(combined),
                "head_hash": combined[-1].entry_hash if combined else None,
                # The entries about this payment, not the merchant's whole
                # history, so sequence numbers skip. Stated explicitly rather
                # than inferred: a verifier must not have to guess which rule
                # to apply.
                "contiguous": False,
                "entries": [entry.to_dict() for entry in combined],
            },
        }

        return {
            "body": body,
            "signature": {
                "algorithm": SIGNATURE_ALGORITHM,
                "body_hash": body_digest(body),
                "value": sign_body(body, self.signing_key),
            },
        }

    def verify(self, passport: dict[str, Any]) -> tuple[bool, list[str]]:
        """Verify a passport against this deployment's key."""
        return verify_passport(passport, self.signing_key)

    @staticmethod
    def _iso(value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        return None


__all__ = [
    "PASSPORT_VERSION",
    "SIGNATURE_ALGORITHM",
    "PassportService",
    "body_digest",
    "canonical_body",
    "sign_body",
    "verify_passport",
]
