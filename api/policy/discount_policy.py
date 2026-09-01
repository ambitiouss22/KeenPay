"""
Discount Policy Engine - Merchant-Defined Discount Bounds

This module enforces merchant-configured discount limits, preventing AI from
proposing discounts outside safe bounds. Implements the "bounded AI" pattern.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class UserType(str, Enum):
    """User segmentation for discount rules"""

    NEW = "new"
    RETURNING = "returning"
    VIP = "vip"
    BULK_BUYER = "bulk_buyer"


@dataclass
class UserSegmentDiscount:
    """Max discount for a specific user segment"""

    user_type: UserType
    max_discount_pct: float  # 0.0 - 100.0
    description: str = ""

    def __post_init__(self):
        if not 0 <= self.max_discount_pct <= 100:
            raise ValueError(f"Discount must be 0-100, got {self.max_discount_pct}")


@dataclass
class DiscountPolicy:
    """Merchant-defined discount bounds for a product"""

    policy_id: str
    merchant_id: str
    product_sku: str

    # Global limit
    max_discount_pct: float  # Absolute max across all users

    # Per-segment limits
    per_user_type: dict[UserType, float] = field(default_factory=dict)
    # Example: {UserType.NEW: 10.0, UserType.VIP: 25.0}

    # Bounds
    daily_budget_paise: int  # Total discount budget per day
    weekly_budget_paise: int = 0  # Optional weekly cap

    # Blacklisted combinations
    blacklist_combos: list[str] = field(default_factory=list)
    # Example: ["free_shipping+50pct_off", "buy_one_get_one+discount"]

    # Policy metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    def get_max_discount_for_user(self, user_type: str) -> float:
        """
        Get maximum discount allowed for a user type.

        Returns the lesser of:
        - Global maximum
        - User-type-specific maximum
        """
        try:
            user_segment = UserType(user_type.lower())
        except ValueError:
            user_segment = UserType.RETURNING  # Default fallback

        segment_max = self.per_user_type.get(user_segment, self.max_discount_pct)
        return min(self.max_discount_pct, segment_max)


@dataclass
class DiscountRequest:
    """Request to apply a discount"""

    merchant_id: str
    product_sku: str
    user_id: str
    user_type: str
    requested_discount_pct: float
    reason: str = ""  # Why AI wants to give this discount
    session_id: str = ""


@dataclass
class DiscountDecision:
    """Result of discount policy check"""

    approved: bool
    approved_discount_pct: float  # What was actually approved
    reason: str  # Explanation for user
    policy_applied: str  # Which rule applied
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self):
        return {
            "approved": self.approved,
            "approved_discount_pct": self.approved_discount_pct,
            "reason": self.reason,
            "policy_applied": self.policy_applied,
            "timestamp": self.timestamp.isoformat(),
        }


class DiscountPolicyEngine:
    """
    Engine for managing and enforcing merchant discount policies.

    Design Principle: Bounded AI
    - AI proposes within merchant-defined limits
    - Not "AI proposes anything → guardrail blocks"
    - But "AI proposes within bounds → guardrail verifies"
    """

    def __init__(self):
        """Initialize policy store (in-memory for now, can be replaced with DB)"""
        self._policies: dict[str, DiscountPolicy] = {}
        self._daily_usage: dict[str, int] = {}  # {policy_id: total_paise_used_today}
        self._weekly_usage: dict[str, int] = {}

    def register_policy(self, policy: DiscountPolicy) -> None:
        """Register a new discount policy"""
        if not policy.is_active:
            raise ValueError("Cannot register inactive policy")

        self._policies[policy.policy_id] = policy

    def get_policy(self, merchant_id: str, product_sku: str) -> DiscountPolicy | None:
        """Retrieve policy for a merchant's product"""
        for policy in self._policies.values():
            if policy.merchant_id == merchant_id and policy.product_sku == product_sku:
                if policy.is_active:
                    return policy
        return None

    def check_discount_request(self, request: DiscountRequest) -> DiscountDecision:
        """
        Check if requested discount is within policy bounds.

        Returns approved discount (may be less than requested).
        """
        # Step 1: Get merchant's policy for this product
        policy = self.get_policy(request.merchant_id, request.product_sku)

        if not policy:
            # No policy = no discounts allowed
            return DiscountDecision(
                approved=False,
                approved_discount_pct=0.0,
                reason="No discount policy configured for this product",
                policy_applied="NO_POLICY",
            )

        # Step 2: Check user type limit
        max_for_user = policy.get_max_discount_for_user(request.user_type)

        if request.requested_discount_pct > max_for_user:
            approved_discount = max_for_user
            reason = f"Your tier ({request.user_type}) gets up to {max_for_user}% off"
            policy_applied = f"USER_TYPE_LIMIT_{request.user_type}"
        else:
            approved_discount = request.requested_discount_pct
            reason = f"Discount approved: {approved_discount}% off"
            policy_applied = f"WITHIN_LIMIT_{request.user_type}"

        # Step 3: Check daily budget
        daily_used = self._daily_usage.get(policy.policy_id, 0)
        approved_amount_paise = int(approved_discount / 100.0)  # Simplified

        if daily_used + approved_amount_paise > policy.daily_budget_paise:
            # Budget exceeded, reduce discount
            remaining_budget = policy.daily_budget_paise - daily_used
            if remaining_budget <= 0:
                approved_discount = 0
                reason = "Daily discount budget exhausted"
                policy_applied = "DAILY_BUDGET_EXCEEDED"
            else:
                # Give partial discount from remaining budget
                approved_discount = min(approved_discount, 50)  # Conservative cap
                reason = f"Daily budget limit: reduced to {approved_discount}%"
                policy_applied = "DAILY_BUDGET_PARTIAL"

        # Step 4: Check blacklisted combinations
        combo_key = f"{approved_discount}pct_off"
        if combo_key in policy.blacklist_combos:
            approved_discount = max(0, approved_discount - 5)  # Reduce by 5%
            reason = f"Cannot combine with other offers. Adjusted to {approved_discount}%"
            policy_applied = "BLACKLIST_COMBO_ADJUSTED"

        # Step 5: Record usage (only if approved)
        if approved_discount > 0:
            self._daily_usage[policy.policy_id] = daily_used + int(approved_discount / 100.0)

        return DiscountDecision(
            approved=approved_discount > 0,
            approved_discount_pct=approved_discount,
            reason=reason,
            policy_applied=policy_applied,
        )

    def reset_daily_budget(self, policy_id: str) -> None:
        """Reset daily usage counter (call at midnight)"""
        if policy_id in self._daily_usage:
            self._daily_usage[policy_id] = 0

    def reset_all_daily_budgets(self) -> None:
        """Reset all daily counters"""
        self._daily_usage.clear()

    def get_usage_stats(self, policy_id: str) -> dict:
        """Get discount usage stats for a policy"""
        policy = next((p for p in self._policies.values() if p.policy_id == policy_id), None)

        if not policy:
            return {}

        daily_used = self._daily_usage.get(policy_id, 0)
        daily_remaining = policy.daily_budget_paise - daily_used

        return {
            "policy_id": policy_id,
            "merchant_id": policy.merchant_id,
            "product_sku": policy.product_sku,
            "daily_budget_paise": policy.daily_budget_paise,
            "daily_used_paise": daily_used,
            "daily_remaining_paise": max(0, daily_remaining),
            "daily_usage_pct": (daily_used / policy.daily_budget_paise * 100)
            if policy.daily_budget_paise > 0
            else 0,
        }


# Singleton instance
_discount_engine = DiscountPolicyEngine()


def get_discount_engine() -> DiscountPolicyEngine:
    """Get singleton instance of discount engine"""
    return _discount_engine
