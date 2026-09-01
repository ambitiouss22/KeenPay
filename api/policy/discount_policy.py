"""
Discount Policy Engine - Merchant-Defined Discount Bounds

This module enforces merchant-configured discount limits, preventing AI from
proposing discounts outside safe bounds. Implements the "bounded AI" pattern.

Design notes
------------
* Global `max_discount_pct` is the default ceiling for any user segment that
  does NOT have an explicit override.
* `per_user_type` entries are deliberate merchant overrides and MAY exceed the
  global ceiling (e.g. global 25%, VIP 35%). The global value is a default,
  not a hard cap, so merchants can reward specific tiers.
* Budget accounting uses a reference product price of INR 1000 (100_000 paise),
  so a 1% discount costs 1000 paise. This keeps percentage-based decisions
  comparable against a paise-denominated daily budget.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# Reference price used to convert a discount percentage into paise.
# INR 1000 product => 1% discount costs 1000 paise.
PAISE_PER_DISCOUNT_PCT = 1000


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

    # Global limit (default ceiling for segments without an override)
    max_discount_pct: float

    # Bounds (no defaults - must come before fields with defaults)
    daily_budget_paise: int  # Total discount budget per day

    # Per-segment overrides. MAY exceed max_discount_pct.
    per_user_type: dict[UserType, float] = field(default_factory=dict)
    # Example: {UserType.VIP: 35.0, UserType.BULK_BUYER: 30.0}

    # Optional bounds
    weekly_budget_paise: int = 0

    # Blacklisted combinations
    blacklist_combos: list[str] = field(default_factory=list)
    # Example: ["free_shipping+50pct_off"]

    # Policy metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    def has_segment_override(self, user_type: str) -> bool:
        """True if this user type has an explicit per-segment limit."""
        return self._resolve_segment(user_type) in self.per_user_type

    def _resolve_segment(self, user_type: str) -> UserType:
        """Map a raw user_type string to a UserType, defaulting to RETURNING."""
        try:
            return UserType(user_type.lower())
        except ValueError:
            return UserType.RETURNING

    def get_max_discount_for_user(self, user_type: str) -> float:
        """
        Maximum discount allowed for a user type.

        An explicit per-segment override wins outright and may exceed the
        global ceiling. Segments without an override fall back to the global
        maximum.
        """
        segment = self._resolve_segment(user_type)

        if segment in self.per_user_type:
            return self.per_user_type[segment]

        return self.max_discount_pct


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
    - Not "AI proposes anything -> guardrail blocks"
    - But "AI proposes within bounds -> guardrail verifies"
    """

    def __init__(self):
        """Initialize policy store (in-memory for now, can be replaced with DB)"""
        self._policies: dict[str, DiscountPolicy] = {}
        self._daily_usage: dict[str, int] = {}  # {policy_id: total_paise_used_today}
        self._weekly_usage: dict[str, int] = {}

    # -- policy management ------------------------------------------------

    def register_policy(self, policy: DiscountPolicy) -> None:
        """Register a new discount policy"""
        if not policy.is_active:
            raise ValueError("Cannot register inactive policy")

        self._policies[policy.policy_id] = policy

    def get_policy(self, merchant_id: str, product_sku: str) -> "DiscountPolicy | None":
        """Retrieve policy for a merchant's product"""
        for policy in self._policies.values():
            if policy.merchant_id == merchant_id and policy.product_sku == product_sku:
                if policy.is_active:
                    return policy
        return None

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _pct_to_paise(discount_pct: float) -> int:
        """Convert a discount percentage into its paise cost."""
        return int(discount_pct * PAISE_PER_DISCOUNT_PCT)

    @staticmethod
    def _paise_to_pct(paise: int) -> float:
        """Convert a paise budget into the discount percentage it can fund."""
        return paise / PAISE_PER_DISCOUNT_PCT

    # -- main entry point -------------------------------------------------

    def check_discount_request(self, request: DiscountRequest) -> DiscountDecision:
        """
        Check if requested discount is within policy bounds.

        Returns the approved discount, which may be less than requested.
        """
        # Step 0: Reject invalid input outright
        if request.requested_discount_pct < 0:
            return DiscountDecision(
                approved=False,
                approved_discount_pct=0.0,
                reason="Negative discounts are not allowed",
                policy_applied="NEGATIVE_DISCOUNT_REJECTED",
            )

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

        # Step 2: Apply the ceiling for this user's segment
        max_for_user = policy.get_max_discount_for_user(request.user_type)

        if request.requested_discount_pct > max_for_user:
            approved_discount = max_for_user

            if policy.has_segment_override(request.user_type):
                # A deliberate per-tier limit produced this number
                reason = f"Your tier ({request.user_type}) gets up to {max_for_user}% off"
                policy_applied = f"USER_TYPE_LIMIT_{request.user_type}"
            else:
                # No tier override, so the global ceiling produced this number
                reason = f"Maximum discount is up to {max_for_user}% off"
                policy_applied = "GLOBAL_MAX_LIMIT"
        else:
            approved_discount = request.requested_discount_pct
            reason = f"Discount approved: {approved_discount}% off"
            policy_applied = f"WITHIN_LIMIT_{request.user_type}"

        # Step 3: Enforce the daily budget
        daily_used = self._daily_usage.get(policy.policy_id, 0)
        projected_cost_paise = self._pct_to_paise(approved_discount)

        if daily_used + projected_cost_paise > policy.daily_budget_paise:
            remaining_budget = policy.daily_budget_paise - daily_used

            if remaining_budget <= 0:
                # Nothing left today: deny outright and record no usage
                return DiscountDecision(
                    approved=False,
                    approved_discount_pct=0.0,
                    reason="Daily discount budget exhausted",
                    policy_applied="DAILY_BUDGET_EXCEEDED",
                )

            # Partial budget left: shrink the discount so it fits
            affordable_discount = self._paise_to_pct(remaining_budget)
            approved_discount = min(approved_discount, affordable_discount)
            reason = f"Daily budget limit: reduced to {approved_discount}% off"
            policy_applied = "DAILY_BUDGET_PARTIAL"

        # Step 4: Check blacklisted combinations
        combo_key = f"{approved_discount}pct_off"
        if combo_key in policy.blacklist_combos:
            approved_discount = max(0.0, approved_discount - 5)
            reason = f"Cannot combine with other offers. Adjusted to {approved_discount}% off"
            policy_applied = "BLACKLIST_COMBO_ADJUSTED"

        # Step 5: Record usage for any non-zero approved discount
        if approved_discount > 0:
            self._daily_usage[policy.policy_id] = daily_used + self._pct_to_paise(
                approved_discount
            )

        # A 0% outcome that reached this point is a valid, approved decision.
        return DiscountDecision(
            approved=True,
            approved_discount_pct=approved_discount,
            reason=reason,
            policy_applied=policy_applied,
        )

    # -- budget maintenance -----------------------------------------------

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
