"""Transaction Passport — Hash-chained audit trail (AegisPay pattern).

Every payment journey is recorded in an immutable, tamper-evident ledger:
- Hash-chained entries (each signs prior entry)
- Append-only (no updates, only new entries)
- Structured as a "passport" per transaction
- Proof of all decision points and transitions

This is what "explainable" means in Track 1.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4


@dataclass
class PassportEntry:
    """One step in the transaction journey."""

    entry_id: str  # UUID, unique within passport
    timestamp: datetime
    actor: str  # "SYSTEM", "USER", "AGENT", "POLICY_ENGINE", "PAYMENT_ENGINE", "HUMAN_APPROVER"
    # "OFFER_PROPOSED", "OFFER_ACCEPTED", "GUARDRAIL_CHECK", "AUTH_CREATED",
    # "PAYMENT_INITIATED", "PAYMENT_CONFIRMED", etc.
    event_type: str

    # Immutable payload (what happened)
    payload: dict  # Context-specific data (offer, decision, reason, etc.)

    # Hash chain (proof of order and integrity)
    prior_entry_hash: str | None = None  # SHA256 of previous entry (None for first)
    entry_hash: str | None = None  # SHA256 of this entry (computed)

    # Metadata
    session_id: str = ""
    order_id: str | None = None
    decision_id: str | None = None  # Link to guardrail decision
    auth_id: str | None = None  # Link to authorization

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this entry (deterministic)."""
        entry_dict = {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "event_type": self.event_type,
            "payload": self.payload,
            "prior_entry_hash": self.prior_entry_hash,
            "session_id": self.session_id,
            "order_id": self.order_id,
            "decision_id": self.decision_id,
            "auth_id": self.auth_id,
        }
        entry_json = json.dumps(entry_dict, sort_keys=True, separators=(",", ":"))
        return sha256(entry_json.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "event_type": self.event_type,
            "payload": self.payload,
            "prior_entry_hash": self.prior_entry_hash,
            "entry_hash": self.entry_hash,
            "session_id": self.session_id,
            "order_id": self.order_id,
            "decision_id": self.decision_id,
            "auth_id": self.auth_id,
        }


@dataclass
class TransactionPassport:
    """Proof of a transaction's entire journey."""

    passport_id: str  # UUID
    transaction_id: str  # order_id or session_id
    merchant_id: str
    created_at: datetime

    # Entries in order
    entries: list[PassportEntry] = field(default_factory=list)

    # Verification
    is_verified: bool = False
    verification_errors: list[str] = field(default_factory=list)

    def add_entry(
        self,
        *,
        actor: str,
        event_type: str,
        payload: dict,
        session_id: str = "",
        order_id: str | None = None,
        decision_id: str | None = None,
        auth_id: str | None = None,
    ) -> PassportEntry:
        """Append a new entry to the passport.

        Args:
            actor: Who triggered this (SYSTEM, USER, AGENT, etc.)
            event_type: What happened (OFFER_PROPOSED, PAYMENT_INITIATED, etc.)
            payload: Structured data for this step
            session_id: Checkout session ID
            order_id: Order ID (if applicable)
            decision_id: Link to guardrail decision (if applicable)
            auth_id: Link to authorization (if applicable)

        Returns:
            New PassportEntry added to passport
        """
        entry_id = str(uuid4())
        now = datetime.now(UTC)

        prior_hash = self.entries[-1].entry_hash if self.entries else None

        entry = PassportEntry(
            entry_id=entry_id,
            timestamp=now,
            actor=actor,
            event_type=event_type,
            payload=payload,
            prior_entry_hash=prior_hash,
            session_id=session_id,
            order_id=order_id,
            decision_id=decision_id,
            auth_id=auth_id,
        )

        # Compute hash
        entry.entry_hash = entry.compute_hash()

        # Append
        self.entries.append(entry)
        return entry

    def verify(self) -> tuple[bool, list[str]]:
        """Verify hash chain integrity.

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        if not self.entries:
            return True, []

        # Check first entry has no prior
        if self.entries[0].prior_entry_hash is not None:
            errors.append("First entry should have no prior_entry_hash")

        # Check hash chain
        for i, entry in enumerate(self.entries):
            # Recompute hash
            computed = entry.compute_hash()
            if entry.entry_hash != computed:
                errors.append(
                    f"Entry {i} hash mismatch: stored={entry.entry_hash}, computed={computed}"
                )

            # Verify prior hash
            if i > 0:
                prior_stored = entry.prior_entry_hash
                prior_actual = self.entries[i - 1].entry_hash
                if prior_stored != prior_actual:
                    errors.append(
                        f"Entry {i} prior_hash broken: stored={prior_stored}, prior={prior_actual}"
                    )

        is_valid = len(errors) == 0
        self.is_verified = is_valid
        self.verification_errors = errors
        return is_valid, errors

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "passport_id": self.passport_id,
            "transaction_id": self.transaction_id,
            "merchant_id": self.merchant_id,
            "created_at": self.created_at.isoformat(),
            "entries": [e.to_dict() for e in self.entries],
            "is_verified": self.is_verified,
            "verification_errors": self.verification_errors,
        }

    def summary(self) -> dict:
        """High-level summary of passport (for display)."""
        if not self.entries:
            return {
                "passport_id": self.passport_id,
                "transaction_id": self.transaction_id,
                "entry_count": 0,
                "events": [],
            }

        return {
            "passport_id": self.passport_id,
            "transaction_id": self.transaction_id,
            "entry_count": len(self.entries),
            "created_at": self.entries[0].timestamp.isoformat(),
            "final_hash": self.entries[-1].entry_hash,
            "events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "actor": e.actor,
                    "event_type": e.event_type,
                }
                for e in self.entries
            ],
            "is_verified": self.is_verified,
        }


class PassportEngine:
    """Issues and maintains transaction passports."""

    def __init__(self):
        """Initialize passport engine.

        In production: persist to DB. For MVP: in-memory.
        """
        # In-memory store: transaction_id -> passport
        self._passports: dict[str, TransactionPassport] = {}

    def create_passport(
        self,
        *,
        transaction_id: str,  # order_id or session_id
        merchant_id: str,
    ) -> TransactionPassport:
        """Create a new transaction passport.

        Args:
            transaction_id: Unique ID for this transaction (order_id)
            merchant_id: Merchant ID

        Returns:
            TransactionPassport object
        """
        passport_id = str(uuid4())
        now = datetime.now(UTC)

        passport = TransactionPassport(
            passport_id=passport_id,
            transaction_id=transaction_id,
            merchant_id=merchant_id,
            created_at=now,
        )

        # Store
        self._passports[transaction_id] = passport
        return passport

    def get_passport(self, transaction_id: str) -> TransactionPassport | None:
        """Retrieve a passport.

        Args:
            transaction_id: Transaction ID

        Returns:
            TransactionPassport or None
        """
        return self._passports.get(transaction_id)

    def add_entry(
        self,
        *,
        transaction_id: str,
        actor: str,
        event_type: str,
        payload: dict,
        session_id: str = "",
        order_id: str | None = None,
        decision_id: str | None = None,
        auth_id: str | None = None,
    ) -> PassportEntry | None:
        """Add an entry to an existing passport.

        Args:
            transaction_id: Transaction ID (must exist)
            actor: Who triggered this
            event_type: What happened
            payload: Structured data
            session_id: Checkout session ID
            order_id: Order ID
            decision_id: Guardrail decision ID
            auth_id: Authorization ID

        Returns:
            PassportEntry if added, None if passport not found
        """
        passport = self.get_passport(transaction_id)
        if not passport:
            return None

        return passport.add_entry(
            actor=actor,
            event_type=event_type,
            payload=payload,
            session_id=session_id,
            order_id=order_id,
            decision_id=decision_id,
            auth_id=auth_id,
        )

    def verify_passport(self, transaction_id: str) -> tuple[bool, list[str]]:
        """Verify integrity of a passport.

        Args:
            transaction_id: Transaction ID

        Returns:
            (is_valid, error_messages)
        """
        passport = self.get_passport(transaction_id)
        if not passport:
            return False, ["Passport not found"]

        return passport.verify()

    def get_summary(self, transaction_id: str) -> dict | None:
        """Get high-level summary of a passport.

        Args:
            transaction_id: Transaction ID

        Returns:
            Summary dict or None
        """
        passport = self.get_passport(transaction_id)
        if not passport:
            return None

        return passport.summary()


# Singleton instance
passport_engine = PassportEngine()
