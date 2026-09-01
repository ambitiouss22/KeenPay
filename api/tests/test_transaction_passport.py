"""Unit tests for Transaction Passport — Hash-chained audit trail."""

import pytest
from api.audit.transaction_passport import (
    PassportEngine,
    PassportEntry,
    TransactionPassport,
)


class TestTransactionPassportEntry:
    """Test individual passport entries."""

    def test_create_entry(self):
        """Create a passport entry."""
        entry = PassportEntry(
            entry_id="e1",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            actor="SYSTEM",
            event_type="CHECKOUT_STARTED",
            payload={"cart_items": 2},
        )

        assert entry.entry_id == "e1"
        assert entry.actor == "SYSTEM"
        assert entry.event_type == "CHECKOUT_STARTED"
        assert entry.payload == {"cart_items": 2}
        assert entry.prior_entry_hash is None

    def test_entry_hash_computation(self):
        """Entry hash is computed deterministically."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        entry = PassportEntry(
            entry_id="e1",
            timestamp=now,
            actor="SYSTEM",
            event_type="START",
            payload={"test": 1},
        )

        hash1 = entry.compute_hash()
        hash2 = entry.compute_hash()

        # Same entry should produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 produces 64-char hex

    def test_entry_hash_unique_per_payload(self):
        """Different payloads produce different hashes."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        entry1 = PassportEntry(
            entry_id="e1",
            timestamp=now,
            actor="SYSTEM",
            event_type="START",
            payload={"test": 1},
        )

        entry2 = PassportEntry(
            entry_id="e2",
            timestamp=now,
            actor="SYSTEM",
            event_type="START",
            payload={"test": 2},
        )

        # Different payloads should produce different hashes
        assert entry1.compute_hash() != entry2.compute_hash()

    def test_entry_to_dict(self):
        """Entry can be serialized to dict."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        entry = PassportEntry(
            entry_id="e1",
            timestamp=now,
            actor="SYSTEM",
            event_type="START",
            payload={"test": 1},
            entry_hash="abc123",
        )

        entry_dict = entry.to_dict()
        assert entry_dict["entry_id"] == "e1"
        assert entry_dict["actor"] == "SYSTEM"
        assert entry_dict["event_type"] == "START"
        assert entry_dict["entry_hash"] == "abc123"
        assert entry_dict["prior_entry_hash"] is None


class TestTransactionPassport:
    """Test transaction passport."""

    def test_create_passport(self):
        """Create a new passport."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        assert passport.passport_id == "pp1"
        assert passport.transaction_id == "tx1"
        assert passport.merchant_id == "m1"
        assert len(passport.entries) == 0

    def test_add_single_entry(self):
        """Add an entry to passport."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        entry = passport.add_entry(
            actor="SYSTEM",
            event_type="CHECKOUT_STARTED",
            payload={"step": 1},
            session_id="s1",
        )

        assert len(passport.entries) == 1
        assert entry.actor == "SYSTEM"
        assert entry.prior_entry_hash is None
        assert entry.entry_hash is not None

    def test_add_multiple_entries_creates_chain(self):
        """Multiple entries form a hash chain."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        entry1 = passport.add_entry(
            actor="SYSTEM",
            event_type="CHECKOUT_STARTED",
            payload={"step": 1},
        )

        entry2 = passport.add_entry(
            actor="SYSTEM",
            event_type="RISK_ASSESSED",
            payload={"score": 0.2},
        )

        entry3 = passport.add_entry(
            actor="USER",
            event_type="PAYMENT_CONFIRMED",
            payload={"user_id": "u1"},
        )

        # Verify chain
        assert entry1.prior_entry_hash is None
        assert entry2.prior_entry_hash == entry1.entry_hash
        assert entry3.prior_entry_hash == entry2.entry_hash

        # All hashes should be unique
        hashes = [e.entry_hash for e in passport.entries]
        assert len(hashes) == len(set(hashes))

    def test_passport_verification_valid_chain(self):
        """Verify intact hash chain."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="START",
            payload={"step": 1},
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="NEXT",
            payload={"step": 2},
        )

        is_valid, errors = passport.verify()
        assert is_valid is True
        assert len(errors) == 0
        assert passport.is_verified is True

    def test_passport_verification_detects_tampering(self):
        """Verification detects hash tampering."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="START",
            payload={"step": 1},
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="NEXT",
            payload={"step": 2},
        )

        # Tamper: change second entry's hash
        passport.entries[1].entry_hash = "tampered_hash"

        is_valid, errors = passport.verify()
        assert is_valid is False
        assert len(errors) > 0
        assert any("hash mismatch" in e.lower() for e in errors)

    def test_passport_verification_detects_broken_chain(self):
        """Verification detects broken hash chain."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="START",
            payload={"step": 1},
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="NEXT",
            payload={"step": 2},
        )

        # Break chain: change second entry's prior_hash
        passport.entries[1].prior_entry_hash = "wrong_prior_hash"

        is_valid, errors = passport.verify()
        assert is_valid is False
        assert len(errors) > 0
        assert any("prior_hash" in e.lower() for e in errors)

    def test_passport_to_dict(self):
        """Passport can be serialized to dict."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="START",
            payload={"step": 1},
        )

        passport_dict = passport.to_dict()
        assert passport_dict["passport_id"] == "pp1"
        assert passport_dict["transaction_id"] == "tx1"
        assert passport_dict["merchant_id"] == "m1"
        assert len(passport_dict["entries"]) == 1

    def test_passport_summary(self):
        """Passport summary shows key info."""
        from datetime import UTC, datetime

        passport = TransactionPassport(
            passport_id="pp1",
            transaction_id="tx1",
            merchant_id="m1",
            created_at=datetime.now(UTC),
        )

        passport.add_entry(
            actor="SYSTEM",
            event_type="START",
            payload={},
        )

        passport.add_entry(
            actor="USER",
            event_type="CONFIRM",
            payload={},
        )

        summary = passport.summary()
        assert summary["passport_id"] == "pp1"
        assert summary["transaction_id"] == "tx1"
        assert summary["entry_count"] == 2
        assert len(summary["events"]) == 2
        assert summary["events"][0]["event_type"] == "START"
        assert summary["events"][1]["event_type"] == "CONFIRM"


class TestPassportEngine:
    """Test passport engine (factory)."""

    def test_create_passport(self):
        """Engine creates passport."""
        engine = PassportEngine()
        passport = engine.create_passport(
            transaction_id="tx1",
            merchant_id="m1",
        )

        assert passport.passport_id is not None
        assert passport.transaction_id == "tx1"
        assert passport.merchant_id == "m1"

    def test_get_passport(self):
        """Engine retrieves stored passport."""
        engine = PassportEngine()
        created = engine.create_passport(transaction_id="tx1", merchant_id="m1")

        retrieved = engine.get_passport("tx1")
        assert retrieved is not None
        assert retrieved.passport_id == created.passport_id

    def test_get_nonexistent_passport(self):
        """Retrieving nonexistent passport returns None."""
        engine = PassportEngine()
        passport = engine.get_passport("nonexistent")
        assert passport is None

    def test_add_entry_to_passport(self):
        """Engine adds entry to existing passport."""
        engine = PassportEngine()
        engine.create_passport(transaction_id="tx1", merchant_id="m1")

        entry = engine.add_entry(
            transaction_id="tx1",
            actor="SYSTEM",
            event_type="START",
            payload={},
        )

        assert entry is not None
        assert entry.event_type == "START"

    def test_add_entry_to_nonexistent_passport(self):
        """Adding entry to nonexistent passport returns None."""
        engine = PassportEngine()
        entry = engine.add_entry(
            transaction_id="tx1",
            actor="SYSTEM",
            event_type="START",
            payload={},
        )

        assert entry is None

    def test_verify_passport(self):
        """Engine verifies passport."""
        engine = PassportEngine()
        passport = engine.create_passport(transaction_id="tx1", merchant_id="m1")

        engine.add_entry(transaction_id="tx1", actor="SYSTEM", event_type="START", payload={})
        engine.add_entry(transaction_id="tx1", actor="USER", event_type="CONFIRM", payload={})

        is_valid, errors = engine.verify_passport("tx1")
        assert is_valid is True
        assert len(errors) == 0

    def test_verify_nonexistent_passport(self):
        """Verifying nonexistent passport returns error."""
        engine = PassportEngine()
        is_valid, errors = engine.verify_passport("nonexistent")
        assert is_valid is False
        assert len(errors) > 0

    def test_get_summary(self):
        """Engine retrieves passport summary."""
        engine = PassportEngine()
        passport = engine.create_passport(transaction_id="tx1", merchant_id="m1")

        engine.add_entry(transaction_id="tx1", actor="SYSTEM", event_type="START", payload={})
        engine.add_entry(transaction_id="tx1", actor="USER", event_type="CONFIRM", payload={})

        summary = engine.get_summary("tx1")
        assert summary is not None
        assert summary["entry_count"] == 2

    def test_get_summary_nonexistent(self):
        """Summary for nonexistent passport returns None."""
        engine = PassportEngine()
        summary = engine.get_summary("nonexistent")
        assert summary is None


class TestPassportIntegration:
    """Integration tests for complete audit trail."""

    def test_complete_checkout_audit_trail(self):
        """Complete checkout flow creates valid audit trail."""
        engine = PassportEngine()

        # Create passport
        passport = engine.create_passport(transaction_id="order_123", merchant_id="merchant_keen")

        # Simulate checkout flow
        engine.add_entry(
            transaction_id="order_123",
            actor="SYSTEM",
            event_type="CHECKOUT_STARTED",
            payload={"cart_items": 2, "amount_paise": 9900},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="SYSTEM",
            event_type="RISK_ASSESSED",
            payload={"score": 0.2, "level": "LOW", "recommendation": "PROCEED"},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="SYSTEM",
            event_type="AUTHORIZATION_CREATED",
            payload={"auth_id": "a123", "cart_hash": "hash123", "ttl": 300},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="AGENT",
            event_type="OFFER_PROPOSED",
            payload={"discount_pct": 10, "final_amount_paise": 8910},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="SYSTEM",
            event_type="GUARDRAIL_CHECK",
            payload={"outcome": "APPROVED", "decision_id": "d123"},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="USER",
            event_type="PAYMENT_CONFIRMED",
            payload={"user_id": "u123"},
        )

        engine.add_entry(
            transaction_id="order_123",
            actor="SYSTEM",
            event_type="PAYMENT_LINK_CREATED",
            payload={"razorpay_link_id": "link123", "auth_id": "a123"},
        )

        # Verify complete trail
        is_valid, errors = engine.verify_passport("order_123")
        assert is_valid is True
        assert len(errors) == 0

        # Check summary
        summary = engine.get_summary("order_123")
        assert summary["entry_count"] == 7
        assert summary["is_verified"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
