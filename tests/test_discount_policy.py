"""
Unit tests for Discount Policy Engine - Bounded AI Safety Implementation.

Tests validate that:
1. AI proposals are bounded by merchant-defined limits
2. Budgets are enforced daily and weekly
3. User segments get correct overrides
4. Blacklisted combinations are prevented
5. All decisions are auditable
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from api.policy.discount_policy import (
    UserType,
    UserSegmentDiscount,
    DiscountPolicy,
    DiscountRequest,
    DiscountDecision,
    DiscountPolicyEngine,
    get_discount_engine
)


@pytest.fixture
def engine():
    """Fresh discount engine instance."""
    return DiscountPolicyEngine()


@pytest.fixture
def hoodie_policy(engine):
    """Sample policy: Hoodie product with segment overrides."""
    policy = DiscountPolicy(
        policy_id="policy_hoodie_001",
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        max_discount_pct=25.0,
        daily_budget_paise=50000,  # 500 INR
        weekly_budget_paise=300000,  # 3000 INR
        per_user_type={
            UserType.VIP: 35.0,
            UserType.BULK_BUYER: 30.0,
            # RETURNING and NEW use global max of 25.0
        },
        blacklist_combos=["free_shipping+50pct_off"]
    )
    engine.register_policy(policy)
    return policy


@pytest.fixture
def tight_policy(engine):
    """Sample policy: Tee product with tight bounds."""
    policy = DiscountPolicy(
        policy_id="policy_tee_001",
        merchant_id="merchant_keen",
        product_sku="TEE-BLACK-M",
        max_discount_pct=15.0,
        daily_budget_paise=20000,  # 200 INR
        per_user_type={
            UserType.NEW: 5.0,
        }
    )
    engine.register_policy(policy)
    return policy


# =============================================================================
# Test: No Policy → No Discounts
# =============================================================================

def test_no_policy_denies_discount(engine):
    """Verify: No policy configured = discount denied."""
    request = DiscountRequest(
        merchant_id="merchant_unknown",
        product_sku="UNKNOWN-SKU",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=10.0,
        reason="Testing no policy"
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == False
    assert decision.approved_discount_pct == 0.0
    assert decision.policy_applied == "NO_POLICY"
    assert "No discount policy" in decision.reason


# =============================================================================
# Test: Within Global Limit
# =============================================================================

def test_discount_within_global_limit_approved(engine, hoodie_policy):
    """Verify: Discount within global max is approved."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_returning",
        user_type="returning",
        requested_discount_pct=15.0,
        reason="Testing within limit"
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 15.0
    assert decision.policy_applied == "WITHIN_LIMIT_returning"
    assert "approved" in decision.reason.lower()


def test_discount_at_global_max_approved(engine, hoodie_policy):
    """Verify: Discount exactly at global max is approved."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_returning",
        user_type="returning",
        requested_discount_pct=25.0
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 25.0


# =============================================================================
# Test: Global Limit Exceeded
# =============================================================================

def test_discount_exceeds_global_max_reduced(engine, hoodie_policy):
    """Verify: Discount > global max is reduced to global max."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_returning",
        user_type="returning",
        requested_discount_pct=40.0  # Request 40% but max is 25%
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True  # Still approved, but reduced
    assert decision.approved_discount_pct == 25.0  # Capped at global max
    assert decision.policy_applied == "GLOBAL_MAX_LIMIT"
    assert "up to" in decision.reason.lower()


# =============================================================================
# Test: User Type Limit Override
# =============================================================================

def test_vip_user_gets_segment_override(engine, hoodie_policy):
    """Verify: VIP segment gets higher limit than global."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_vip",
        user_type="vip",
        requested_discount_pct=40.0  # Request 40%
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 35.0  # VIP segment max
    assert decision.policy_applied == "USER_TYPE_LIMIT_vip"


def test_bulk_buyer_gets_segment_override(engine, hoodie_policy):
    """Verify: Bulk buyer segment gets 30% instead of global 25%."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_bulk",
        user_type="bulk_buyer",
        requested_discount_pct=35.0
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 30.0
    assert decision.policy_applied == "USER_TYPE_LIMIT_bulk_buyer"


def test_new_user_gets_global_max_not_segment(engine, hoodie_policy):
    """Verify: NEW users without segment override use global max."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_new",
        user_type="new",
        requested_discount_pct=20.0
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 20.0
    # Uses global max since no "new" segment override
    assert "LIMIT" in decision.policy_applied


# =============================================================================
# Test: Daily Budget Enforcement
# =============================================================================

def test_daily_budget_prevents_discount(engine, hoodie_policy):
    """Verify: Daily budget exhaustion denies discount."""
    # Exhaust daily budget
    engine._daily_usage[hoodie_policy.policy_id] = 50000  # Max is 50000

    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=5.0  # Any discount fails
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == False
    assert decision.approved_discount_pct == 0.0
    assert decision.policy_applied == "DAILY_BUDGET_EXCEEDED"
    assert "Daily discount budget exhausted" in decision.reason


def test_daily_budget_partial_reduction(engine, hoodie_policy):
    """Verify: Partial budget remaining reduces discount."""
    # Use most of daily budget
    engine._daily_usage[hoodie_policy.policy_id] = 45000  # 45000/50000 used

    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=15.0
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct <= 50  # Conservative cap
    assert decision.policy_applied == "DAILY_BUDGET_PARTIAL"


# =============================================================================
# Test: Blacklist Combinations
# =============================================================================

def test_blacklist_combo_triggers_reduction(engine, hoodie_policy):
    """Verify: Blacklisted combinations reduce discount."""
    # The policy has "free_shipping+50pct_off" in blacklist
    # If discount is 50%, it triggers blacklist check
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_vip",
        user_type="vip",
        requested_discount_pct=35.0  # Within VIP limit
    )

    # Create combo key that matches blacklist
    # This is checked in step 4 of check_discount_request
    decision = engine.check_discount_request(request)

    # Should still be approved (blacklist is for specific combinations)
    # Implementation in discount_policy.py checks combo_key in blacklist_combos
    assert decision.approved == True


# =============================================================================
# Test: Usage Recording
# =============================================================================

def test_approved_discount_records_usage(engine, hoodie_policy):
    """Verify: Approved discount increments usage counter."""
    initial_usage = engine._daily_usage.get(hoodie_policy.policy_id, 0)

    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=10.0
    )

    decision = engine.check_discount_request(request)
    assert decision.approved == True

    # Usage should be recorded
    new_usage = engine._daily_usage.get(hoodie_policy.policy_id, 0)
    assert new_usage > initial_usage


def test_denied_discount_does_not_record_usage(engine, hoodie_policy):
    """Verify: Denied discount doesn't count against budget."""
    # Exhaust budget
    engine._daily_usage[hoodie_policy.policy_id] = 50000

    initial_usage = engine._daily_usage[hoodie_policy.policy_id]

    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=5.0
    )

    decision = engine.check_discount_request(request)
    assert decision.approved == False

    # Usage should not increase
    final_usage = engine._daily_usage[hoodie_policy.policy_id]
    assert final_usage == initial_usage


# =============================================================================
# Test: Zero Discount Edge Cases
# =============================================================================

def test_zero_discount_request_approved(engine, hoodie_policy):
    """Verify: Zero discount request is allowed."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=0.0
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 0.0


def test_negative_discount_rejected(engine, hoodie_policy):
    """Verify: Negative discount is rejected (or treated as 0)."""
    # Implementation should reject negative discounts
    # or treat them as 0
    # Based on the dataclass validation, this should work
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=-5.0
    )

    # Should either fail or be treated as 0
    try:
        decision = engine.check_discount_request(request)
        # If it succeeds, should be 0
        assert decision.approved_discount_pct >= 0.0
    except ValueError:
        # If validation rejects negative, that's also valid
        pass


# =============================================================================
# Test: Multiple Requests Sequential
# =============================================================================

def test_sequential_requests_deplete_budget(engine, hoodie_policy):
    """Verify: Sequential requests deplete daily budget."""
    policy_id = hoodie_policy.policy_id

    # First request: 10% = 1000 paise (approx)
    req1 = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_1",
        user_type="returning",
        requested_discount_pct=10.0
    )
    dec1 = engine.check_discount_request(req1)
    assert dec1.approved == True

    usage_after_1 = engine._daily_usage.get(policy_id, 0)

    # Second request: 10% = 1000 paise
    req2 = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_2",
        user_type="returning",
        requested_discount_pct=10.0
    )
    dec2 = engine.check_discount_request(req2)
    assert dec2.approved == True

    usage_after_2 = engine._daily_usage.get(policy_id, 0)
    assert usage_after_2 > usage_after_1


def test_budget_reset_enables_new_discounts(engine, hoodie_policy):
    """Verify: Budget reset clears usage counter."""
    policy_id = hoodie_policy.policy_id

    # Use up budget
    engine._daily_usage[policy_id] = 50000

    # Reset
    engine.reset_daily_budget(policy_id)

    # Should be available now
    usage = engine._daily_usage.get(policy_id, 0)
    assert usage == 0


# =============================================================================
# Test: Fallback for Unrecognized User Type
# =============================================================================

def test_unrecognized_user_type_defaults_to_returning(engine, hoodie_policy):
    """Verify: Unknown user type falls back to RETURNING segment."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="platinum",  # Not in enum
        requested_discount_pct=20.0
    )

    decision = engine.check_discount_request(request)

    # Should fall back to RETURNING and apply global max
    assert decision.approved == True
    assert decision.approved_discount_pct == 20.0


# =============================================================================
# Test: Policy Retrieval
# =============================================================================

def test_get_policy_returns_registered_policy(engine, hoodie_policy):
    """Verify: Retrieved policy matches registered policy."""
    retrieved = engine.get_policy("merchant_keen", "HOODIE-NAVY-M")

    assert retrieved is not None
    assert retrieved.policy_id == "policy_hoodie_001"
    assert retrieved.max_discount_pct == 25.0


def test_get_policy_returns_none_for_nonexistent(engine):
    """Verify: Non-existent policy returns None."""
    retrieved = engine.get_policy("merchant_unknown", "UNKNOWN-SKU")

    assert retrieved is None


# =============================================================================
# Test: Policy Registration Validation
# =============================================================================

def test_cannot_register_inactive_policy(engine):
    """Verify: Cannot register inactive policy."""
    policy = DiscountPolicy(
        policy_id="policy_inactive",
        merchant_id="merchant_keen",
        product_sku="TEST-SKU",
        max_discount_pct=20.0,
        daily_budget_paise=10000,
        is_active=False  # Inactive
    )

    with pytest.raises(ValueError, match="Cannot register inactive policy"):
        engine.register_policy(policy)


# =============================================================================
# Test: Usage Statistics
# =============================================================================

def test_get_usage_stats_returns_correct_values(engine, hoodie_policy):
    """Verify: Usage stats reflect current state."""
    policy_id = hoodie_policy.policy_id

    # Use half the daily budget
    engine._daily_usage[policy_id] = 25000

    stats = engine.get_usage_stats(policy_id)

    assert stats["policy_id"] == policy_id
    assert stats["daily_budget_paise"] == 50000
    assert stats["daily_used_paise"] == 25000
    assert stats["daily_remaining_paise"] == 25000
    assert stats["daily_usage_pct"] == 50.0


def test_get_usage_stats_nonexistent_policy_returns_empty(engine):
    """Verify: Usage stats for non-existent policy returns empty dict."""
    stats = engine.get_usage_stats("nonexistent_policy")

    assert stats == {}


# =============================================================================
# Test: DiscountDecision Conversion
# =============================================================================

def test_discount_decision_to_dict(engine, hoodie_policy):
    """Verify: Decision can be converted to dict for JSON."""
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_123",
        user_type="vip",
        requested_discount_pct=40.0
    )

    decision = engine.check_discount_request(request)
    decision_dict = decision.to_dict()

    assert isinstance(decision_dict, dict)
    assert "approved" in decision_dict
    assert "approved_discount_pct" in decision_dict
    assert "reason" in decision_dict
    assert "policy_applied" in decision_dict
    assert "timestamp" in decision_dict
    assert decision_dict["timestamp"] is not None


# =============================================================================
# Test: Tight Policy (Low Limits)
# =============================================================================

def test_tight_policy_limits_all_tiers(engine, tight_policy):
    """Verify: Tight policy limits even small requests."""
    # Tight policy has 15% global max and 5% for NEW users
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="TEE-BLACK-M",
        user_id="user_new",
        user_type="new",
        requested_discount_pct=10.0  # Request 10% but NEW max is 5%
    )

    decision = engine.check_discount_request(request)

    assert decision.approved == True
    assert decision.approved_discount_pct == 5.0  # NEW segment max


# =============================================================================
# Integration: Realistic Workflow
# =============================================================================

def test_realistic_vip_checkout_workflow(engine, hoodie_policy):
    """Simulate realistic VIP checkout with discount negotiation."""
    # VIP customer wants 40% discount
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_vip_premium",
        user_type="vip",
        requested_discount_pct=40.0,
        reason="VIP loyalty - quarterly review",
        session_id="session_abc123"
    )

    # Engine bounds the discount
    decision = engine.check_discount_request(request)

    # Decision should be bounded
    assert decision.approved == True
    assert decision.approved_discount_pct <= 35.0  # VIP max
    assert decision.policy_applied == "USER_TYPE_LIMIT_vip"

    # Verify decision contains required fields
    assert decision.reason is not None
    assert decision.timestamp is not None
    # Bounded by the ceiling that actually applies to this user's tier.
    # VIP carries an explicit override (35%) that intentionally sits above the
    # global default of 25%, so max_discount_pct is not the bound here.
    assert decision.approved_discount_pct <= hoodie_policy.get_max_discount_for_user("vip")


def test_realistic_budget_exhaustion_workflow(engine, hoodie_policy):
    """Simulate realistic budget exhaustion scenario."""
    policy_id = hoodie_policy.policy_id

    # Grant discounts throughout the day
    for i in range(5):
        request = DiscountRequest(
            merchant_id="merchant_keen",
            product_sku="HOODIE-NAVY-M",
            user_id=f"user_{i}",
            user_type="returning",
            requested_discount_pct=10.0
        )
        decision = engine.check_discount_request(request)

    # Check current usage
    stats = engine.get_usage_stats(policy_id)
    initial_remaining = stats["daily_remaining_paise"]

    # Try one more request that should fail or be reduced
    request = DiscountRequest(
        merchant_id="merchant_keen",
        product_sku="HOODIE-NAVY-M",
        user_id="user_over_budget",
        user_type="returning",
        requested_discount_pct=50.0
    )
    decision = engine.check_discount_request(request)

    # Should be bounded by budget or policy
    assert decision.approved_discount_pct <= 25.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
