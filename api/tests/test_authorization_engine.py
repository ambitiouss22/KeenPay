"""Unit tests for Authorization Engine — Scoped payment authorization."""

from datetime import UTC, datetime

import pytest
from api.policy.authorization_engine import AuthorizationEngine


class TestAuthorizationEngineCreation:
    """Test authorization creation."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=300)

    def test_create_authorization(self):
        """Create a valid authorization."""
        cart_items = [
            {"sku": "HOODIE-RED", "qty": 1, "price": 9900},
        ]
        auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=cart_items,
            amount_paise=9900,
            currency="INR",
        )

        assert auth.auth_id is not None
        assert auth.session_id == "s1"
        assert auth.merchant_id == "m1"
        assert auth.amount_paise == 9900
        assert auth.currency == "INR"
        assert auth.status == "AUTHORIZED"
        assert auth.one_time_use is True
        assert auth.cart_hash is not None

    def test_authorization_expires_in_ttl(self):
        """Authorization expires after TTL."""
        ttl = 300  # 5 minutes
        auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[],
            amount_paise=100,
        )

        # Should not be expired yet
        assert not auth.is_expired()

        # Check TTL is set correctly
        time_diff = (auth.expires_at - auth.created_at).total_seconds()
        assert time_diff == ttl

    def test_authorization_cart_hash_deterministic(self):
        """Same cart always produces same hash."""
        cart = [
            {"sku": "HOODIE", "qty": 1, "price": 9900},
            {"sku": "TEE", "qty": 2, "price": 2000},
        ]

        auth1 = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=cart,
            amount_paise=13900,
        )

        auth2 = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=cart,
            amount_paise=13900,
        )

        # Same cart should produce same hash
        assert auth1.cart_hash == auth2.cart_hash

    def test_authorization_cart_hash_different_for_different_carts(self):
        """Different carts produce different hashes."""
        cart1 = [{"sku": "HOODIE", "qty": 1, "price": 9900}]
        cart2 = [{"sku": "TEE", "qty": 1, "price": 2000}]

        auth1 = self.engine.create_authorization(
            session_id="s1", merchant_id="m1", cart_items=cart1, amount_paise=9900
        )

        auth2 = self.engine.create_authorization(
            session_id="s1", merchant_id="m1", cart_items=cart2, amount_paise=2000
        )

        # Different carts should produce different hashes
        assert auth1.cart_hash != auth2.cart_hash


class TestAuthorizationEngineValidation:
    """Test authorization validation."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=300)
        self.cart_items = [{"sku": "HOODIE", "qty": 1, "price": 9900}]
        self.auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=self.cart_items,
            amount_paise=9900,
        )

    def test_validate_valid_authorization(self):
        """Valid authorization passes validation."""
        is_valid, error = self.engine.validate_authorization(
            self.auth.auth_id,
            amount_paise=9900,
            cart_items=self.cart_items,
        )
        assert is_valid is True
        assert error is None

    def test_validate_expired_authorization(self):
        """Expired authorization fails validation."""
        # Create an auth with 0 TTL (immediately expired)
        engine = AuthorizationEngine(ttl_seconds=0)
        auth = engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=self.cart_items,
            amount_paise=9900,
        )

        is_valid, error = engine.validate_authorization(
            auth.auth_id,
            amount_paise=9900,
            cart_items=self.cart_items,
        )
        # Should be expired
        assert is_valid is False
        assert "expired" in error.lower()

    def test_validate_amount_mismatch(self):
        """Authorization fails if amount doesn't match."""
        is_valid, error = self.engine.validate_authorization(
            self.auth.auth_id,
            amount_paise=5000,  # Different amount
            cart_items=self.cart_items,
        )
        assert is_valid is False
        assert "amount" in error.lower()

    def test_validate_cart_tampering(self):
        """Authorization fails if cart is modified."""
        tampered_cart = [
            {"sku": "HOODIE", "qty": 2, "price": 9900},  # Qty changed
        ]
        is_valid, error = self.engine.validate_authorization(
            self.auth.auth_id,
            amount_paise=9900,
            cart_items=tampered_cart,
        )
        assert is_valid is False
        assert "modified" in error.lower()

    def test_validate_nonexistent_authorization(self):
        """Validation fails for nonexistent auth."""
        is_valid, error = self.engine.validate_authorization(
            "fake-auth-id",
            amount_paise=9900,
            cart_items=self.cart_items,
        )
        assert is_valid is False
        assert "not found" in error.lower()


class TestAuthorizationEngineConsumption:
    """Test authorization consumption (single-use)."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=300)
        self.auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[{"sku": "HOODIE", "qty": 1}],
            amount_paise=9900,
        )

    def test_consume_authorization(self):
        """Consume a valid authorization."""
        success = self.engine.consume_authorization(self.auth.auth_id)
        assert success is True

        # Check status changed to CONSUMED
        auth = self.engine._authorizations[self.auth.auth_id]
        assert auth.status == "CONSUMED"
        assert auth.consumed_at is not None

    def test_consume_sets_consumed_at_timestamp(self):
        """Consumed_at is set when consumed."""
        before = datetime.now(UTC)
        self.engine.consume_authorization(self.auth.auth_id)
        after = datetime.now(UTC)

        auth = self.engine._authorizations[self.auth.auth_id]
        assert before <= auth.consumed_at <= after

    def test_cannot_reuse_consumed_authorization(self):
        """Cannot use an authorization after consuming it."""
        # Consume
        self.engine.consume_authorization(self.auth.auth_id)

        # Try to validate
        is_valid, error = self.engine.validate_authorization(
            self.auth.auth_id,
            amount_paise=9900,
            cart_items=[{"sku": "HOODIE", "qty": 1}],
        )
        assert is_valid is False
        assert "CONSUMED" in error

    def test_consume_nonexistent_authorization(self):
        """Cannot consume nonexistent auth."""
        success = self.engine.consume_authorization("fake-auth-id")
        assert success is False


class TestAuthorizationEngineRevocation:
    """Test authorization revocation."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=300)
        self.auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[{"sku": "HOODIE", "qty": 1}],
            amount_paise=9900,
        )

    def test_revoke_authorization(self):
        """Revoke a valid authorization."""
        success = self.engine.revoke_authorization(self.auth.auth_id)
        assert success is True

        # Check status changed to REVOKED
        auth = self.engine._authorizations[self.auth.auth_id]
        assert auth.status == "REVOKED"

    def test_cannot_use_revoked_authorization(self):
        """Cannot use a revoked authorization."""
        self.engine.revoke_authorization(self.auth.auth_id)

        is_valid, error = self.engine.validate_authorization(
            self.auth.auth_id,
            amount_paise=9900,
            cart_items=[{"sku": "HOODIE", "qty": 1}],
        )
        assert is_valid is False
        assert "REVOKED" in error

    def test_revoke_nonexistent_authorization(self):
        """Cannot revoke nonexistent auth."""
        success = self.engine.revoke_authorization("fake-auth-id")
        assert success is False


class TestAuthorizationEngineCleanup:
    """Test cleanup of expired authorizations."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=1)  # 1 second TTL

    def test_cleanup_expired(self):
        """Cleanup marks expired auths as EXPIRED."""
        # Create auth with 1 second TTL
        auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[],
            amount_paise=9900,
        )

        # Wait for expiry
        import time

        time.sleep(1.1)

        # Cleanup
        count = self.engine.cleanup_expired()
        assert count >= 1

        # Check status changed
        expired_auth = self.engine._authorizations[auth.auth_id]
        assert expired_auth.status == "EXPIRED"

    def test_cleanup_ignores_consumed(self):
        """Cleanup doesn't touch CONSUMED auths."""
        auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[],
            amount_paise=9900,
        )

        # Consume before expiry
        self.engine.consume_authorization(auth.auth_id)

        import time

        time.sleep(1.1)

        # Cleanup
        self.engine.cleanup_expired()

        # Status should still be CONSUMED
        consumed_auth = self.engine._authorizations[auth.auth_id]
        assert consumed_auth.status == "CONSUMED"


class TestAuthorizationSerialization:
    """Test authorization serialization."""

    def setup_method(self):
        self.engine = AuthorizationEngine(ttl_seconds=300)
        self.auth = self.engine.create_authorization(
            session_id="s1",
            merchant_id="m1",
            cart_items=[{"sku": "HOODIE", "qty": 1}],
            amount_paise=9900,
        )

    def test_to_dict(self):
        """Authorization can be serialized to dict."""
        auth_dict = self.auth.to_dict()

        assert auth_dict["auth_id"] == self.auth.auth_id
        assert auth_dict["session_id"] == "s1"
        assert auth_dict["merchant_id"] == "m1"
        assert auth_dict["amount_paise"] == 9900
        assert auth_dict["status"] == "AUTHORIZED"
        assert auth_dict["cart_hash"] == self.auth.cart_hash
        assert "created_at" in auth_dict
        assert "expires_at" in auth_dict

    def test_metadata_in_serialization(self):
        """Metadata is included in serialization."""
        auth_dict = self.auth.to_dict()
        assert auth_dict["metadata"] == self.auth.metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
