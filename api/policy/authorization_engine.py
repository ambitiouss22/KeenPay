"""Authorization Engine — Scoped payment authorizations (AegisPay pattern).

Every payment is bound to a specific authorization:
- Scoped to a cart hash (items + amounts immutable)
- Single-use (one payment per auth)
- Expires after 5 minutes
- Cannot be modified or reused

This is what "gated" means in Track 1.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Optional
from uuid import uuid4


@dataclass
class Authorization:
    """A scoped payment authorization."""

    auth_id: str  # UUID
    session_id: str
    merchant_id: str

    # Scope binding (immutable)
    cart_hash: str  # SHA256 of cart JSON (prevents tampering)
    amount_paise: int
    currency: str

    # Lifecycle
    status: str  # CREATED, AUTHORIZED, CONSUMED, EXPIRED, REVOKED
    created_at: datetime
    expires_at: datetime
    consumed_at: Optional[datetime] = None

    # Security
    one_time_use: bool = True  # Can only be used once
    metadata: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Check if authorization is still valid."""
        return (
            self.status == "AUTHORIZED" and
            datetime.now(UTC) < self.expires_at
        )

    def is_expired(self) -> bool:
        """Check if authorization has expired."""
        return datetime.now(UTC) > self.expires_at

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "auth_id": self.auth_id,
            "session_id": self.session_id,
            "merchant_id": self.merchant_id,
            "cart_hash": self.cart_hash,
            "amount_paise": self.amount_paise,
            "currency": self.currency,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
            "metadata": self.metadata,
        }


class AuthorizationEngine:
    """Issues and validates payment authorizations."""

    def __init__(self, ttl_seconds: int = 300):  # 5 minute default
        """Initialize authorization engine.

        Args:
            ttl_seconds: Authorization time-to-live in seconds
        """
        self.ttl_seconds = ttl_seconds
        # In production: persist to DB. For MVP: in-memory (will lose on restart)
        self._authorizations: dict[str, Authorization] = {}

    def create_authorization(
        self,
        *,
        session_id: str,
        merchant_id: str,
        cart_items: list[dict],
        amount_paise: int,
        currency: str = "INR",
    ) -> Authorization:
        """Create a new scoped authorization.

        Args:
            session_id: Checkout session ID
            merchant_id: Merchant ID
            cart_items: List of items in cart (with SKU, qty, price)
            amount_paise: Final payment amount in paise
            currency: Currency code

        Returns:
            Authorization object
        """
        # Compute immutable cart hash (prevents tampering)
        cart_json = self._serialize_cart(cart_items)
        cart_hash = sha256(cart_json.encode()).hexdigest()

        auth_id = str(uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.ttl_seconds)

        auth = Authorization(
            auth_id=auth_id,
            session_id=session_id,
            merchant_id=merchant_id,
            cart_hash=cart_hash,
            amount_paise=amount_paise,
            currency=currency,
            status="AUTHORIZED",
            created_at=now,
            expires_at=expires_at,
            metadata={
                "cart_items_count": len(cart_items),
                "ttl_seconds": self.ttl_seconds,
            },
        )

        # Store (in production: write to DB)
        self._authorizations[auth_id] = auth
        return auth

    def validate_authorization(
        self,
        auth_id: str,
        amount_paise: int,
        cart_items: list[dict],
    ) -> tuple[bool, Optional[str]]:
        """Validate authorization before payment.

        Args:
            auth_id: Authorization ID to validate
            amount_paise: Amount trying to charge
            cart_items: Current cart to verify against

        Returns:
            (is_valid, error_message)
        """
        auth = self._authorizations.get(auth_id)

        if not auth:
            return False, f"Authorization {auth_id} not found"

        if auth.status != "AUTHORIZED":
            return False, f"Authorization status is {auth.status}, not AUTHORIZED"

        if auth.is_expired():
            return False, f"Authorization expired at {auth.expires_at.isoformat()}"

        # Verify amount matches
        if auth.amount_paise != amount_paise:
            return False, f"Amount mismatch: auth {auth.amount_paise}, request {amount_paise}"

        # Verify cart hasn't changed
        current_cart_hash = sha256(
            self._serialize_cart(cart_items).encode()
        ).hexdigest()
        if auth.cart_hash != current_cart_hash:
            return False, "Cart contents have been modified since authorization"

        return True, None

    def consume_authorization(self, auth_id: str) -> bool:
        """Mark authorization as consumed (used for payment).

        Args:
            auth_id: Authorization to consume

        Returns:
            True if consumed successfully
        """
        auth = self._authorizations.get(auth_id)

        if not auth:
            return False

        if auth.status != "AUTHORIZED":
            return False

        if not auth.is_valid():
            return False

        # Mark as consumed
        auth.status = "CONSUMED"
        auth.consumed_at = datetime.now(UTC)
        return True

    def revoke_authorization(self, auth_id: str) -> bool:
        """Revoke an authorization (e.g., user cancels).

        Args:
            auth_id: Authorization to revoke

        Returns:
            True if revoked successfully
        """
        auth = self._authorizations.get(auth_id)

        if not auth:
            return False

        auth.status = "REVOKED"
        return True

    def cleanup_expired(self) -> int:
        """Remove expired authorizations.

        Returns:
            Number of expired authorizations cleaned up
        """
        now = datetime.now(UTC)
        expired = [
            auth_id
            for auth_id, auth in self._authorizations.items()
            if auth.is_expired() and auth.status != "CONSUMED"
        ]

        for auth_id in expired:
            self._authorizations[auth_id].status = "EXPIRED"

        return len(expired)

    def _serialize_cart(self, items: list[dict]) -> str:
        """Serialize cart for hashing (deterministic JSON)."""
        import json
        return json.dumps(items, sort_keys=True, separators=(",", ":"))


# Singleton instance
authorization_engine = AuthorizationEngine(ttl_seconds=300)
