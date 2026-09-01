# Discount Policy Integration Guide (Bounded AI Safety Fix)

## Overview

This document explains how to integrate the **Discount Policy Engine** into KeenPay, fixing the critical safety gap where AI could propose unbounded discounts without merchant-defined constraints.

### The Problem (Safety Gap)
- **Before:** AI could propose ANY discount percentage → Guardrails only BLOCK → No guardrails = unlimited proposals
- **After:** Merchant pre-defines bounds → AI proposes ONLY within bounds → Guardrails VERIFY compliance

### The Solution: Bounded AI Pattern
Instead of "AI proposes anything → reactive guardrail blocks", we implement "AI proposes within merchant bounds → proactive guardrail verifies".

---

## Architecture

### Components

1. **DiscountPolicyEngine** (`api/policy/discount_policy.py`)
   - In-memory singleton enforcing merchant discount policies
   - Validates every discount request against policy
   - Returns approved discount (may be < requested)

2. **Database Schema** (`docs/DISCOUNT_POLICY_SCHEMA.sql`)
   - `discount_policies`: Merchant-defined bounds per product
   - `discount_segments`: Per-user-type (new/returning/vip/bulk) overrides
   - `discount_usage_tracking`: Daily/weekly budget consumption
   - `discount_decisions`: Audit trail (append-only)

3. **Session Integration** (`api/services/session.py`)
   - Negotiate node calls `discount_policy_engine.check_discount_request()`
   - Records decision in `discount_decisions` table
   - Passport records the decision for audit trail

4. **Transaction Passport Extension**
   - Each discount decision recorded as passport entry
   - Hash chain includes discount decision details
   - Proof of bounded compliance

---

## Step 1: Database Setup

### Apply Discount Policy Schema

```bash
# From your KeenPay project root:
psql $DATABASE_URL -f docs/DISCOUNT_POLICY_SCHEMA.sql
```

### Verify Tables Created

```sql
-- Connect to your database
psql $DATABASE_URL

-- List discount policy tables
\dt discount_*
\dt passport_*

-- Should see:
-- discount_policies
-- discount_segments
-- discount_usage_tracking
-- discount_decisions
```

### Create Initial Merchant Policies

```sql
-- Example: Define policy for Hoodie product
INSERT INTO discount_policies (
    merchant_id, product_sku, max_discount_pct, daily_budget_paise, weekly_budget_paise, description
) VALUES (
    'merchant_keen',                -- Merchant ID
    'HOODIE-NAVY-M',               -- Product SKU
    25.0,                          -- Max discount: 25%
    50000,                         -- Daily budget: 500 INR (50000 paise)
    300000,                        -- Weekly budget: 3000 INR
    'Hoodie Navy M: Up to 25% off, 500 INR/day'
);

-- Get the policy_id (for segment creation)
SELECT policy_id FROM discount_policies 
WHERE merchant_id = 'merchant_keen' AND product_sku = 'HOODIE-NAVY-M';

-- Add segment overrides (e.g., VIPs get more)
INSERT INTO discount_segments (policy_id, user_segment, max_discount_pct, description)
VALUES (
    '{{policy_id_from_above}}',
    'vip',
    35.0,
    'VIP users: up to 35% off (overrides 25% global)'
);

INSERT INTO discount_segments (policy_id, user_segment, max_discount_pct, description)
VALUES (
    '{{policy_id_from_above}}',
    'bulk_buyer',
    30.0,
    'Bulk buyers: up to 30% off'
);
```

---

## Step 2: Discount Policy Engine

### Already Implemented

The file `api/policy/discount_policy.py` contains:

```python
from api.policy.discount_policy import (
    UserType,
    UserSegmentDiscount,
    DiscountPolicy,
    DiscountRequest,
    DiscountDecision,
    DiscountPolicyEngine,
    get_discount_engine
)
```

### Core Usage

```python
from api.policy.discount_policy import get_discount_engine

# Get singleton instance
engine = get_discount_engine()

# Create policy programmatically
policy = DiscountPolicy(
    policy_id="policy_hoodie_001",
    merchant_id="merchant_keen",
    product_sku="HOODIE-NAVY-M",
    max_discount_pct=25.0,
    daily_budget_paise=50000,
    per_user_type={
        UserType.VIP: 35.0,
        UserType.BULK_BUYER: 30.0,
    },
    blacklist_combos=["free_shipping+50pct_off"]
)

# Register policy
engine.register_policy(policy)

# Request discount (from negotiate node)
request = DiscountRequest(
    merchant_id="merchant_keen",
    product_sku="HOODIE-NAVY-M",
    user_id="user_123",
    user_type="vip",
    requested_discount_pct=40.0,  # AI proposes 40%
    reason="Customer loyalty program",
    session_id="session_abc"
)

# Check discount (this is where bounded AI happens)
decision = engine.check_discount_request(request)

# Result example:
# {
#   "approved": True,
#   "approved_discount_pct": 35.0,  # VIP max, not 40
#   "reason": "Your tier (vip) gets up to 35% off",
#   "policy_applied": "USER_TYPE_LIMIT_vip"
# }
```

### Policy Enforcement Flow

```
1. AI proposes discount
   ↓
2. DiscountPolicyEngine.check_discount_request()
   ├─ Step 1: Get policy for merchant+product
   ├─ Step 2: Check user type limit (min of global and segment max)
   ├─ Step 3: Check daily budget (reduce if exhausted)
   ├─ Step 4: Check blacklisted combinations
   ├─ Step 5: Record usage
   └─ Return decision (approved_discount <= requested_discount)
   ↓
3. Guardrail verifies decision (not blocks)
   ↓
4. User sees approved discount
```

---

## Step 3: Session Service Integration

### Update `api/services/session.py`

#### Import Discount Engine

```python
# Add to imports at top of session.py
from api.policy.discount_policy import (
    get_discount_engine, 
    DiscountRequest,
    UserType
)
```

#### Initialize in `SessionService.__init__()`

```python
def __init__(self):
    # ... existing initialization ...
    self._discount_engine = get_discount_engine()
```

#### Update Negotiate Node

Find the `negotiate()` node and add discount policy check:

```python
@self.langgraph_app.node
async def negotiate(self, state: KeenPayState) -> KeenPayState:
    """
    Negotiate offer within bounded AI constraints.
    """
    # ... existing negotiation logic ...
    
    # Proposed discount from AI
    proposed_discount_pct = state.proposed_offer.get("discount_pct", 0.0)
    
    # NEW: Check against discount policy
    discount_request = DiscountRequest(
        merchant_id=state.merchant_id,
        product_sku=state.selected_line_items[0]["sku"],  # First item SKU
        user_id=state.user_id,
        user_type=self._categorize_user_type(state.user_id),  # NEW function
        requested_discount_pct=proposed_discount_pct,
        reason=f"AI negotiation round {state.negotiation_round}",
        session_id=str(state.id)
    )
    
    discount_decision = self._discount_engine.check_discount_request(discount_request)
    
    # Update offer with approved discount
    if discount_decision.approved:
        state.proposed_offer["discount_pct"] = discount_decision.approved_discount_pct
        state.proposed_offer["discount_reason"] = discount_decision.reason
        state.proposed_offer["policy_applied"] = discount_decision.policy_applied
    else:
        state.proposed_offer["discount_pct"] = 0.0
        state.proposed_offer["discount_reason"] = discount_decision.reason
    
    # Record in audit log
    await self._db.audit_logs.insert_one({
        "session_id": state.id,
        "actor": "policy_engine",
        "action": "DISCOUNT_POLICY_CHECK",
        "input_snapshot": {
            "requested_discount_pct": proposed_discount_pct,
            "user_type": discount_request.user_type
        },
        "output_snapshot": discount_decision.to_dict(),
        "created_at": datetime.now(timezone.utc)
    })
    
    return state
```

#### Add Helper Function

```python
def _categorize_user_type(self, user_id: str) -> str:
    """
    Categorize user into segment: new, returning, vip, bulk_buyer.
    
    This is a stub - implement based on your user data:
    - Check purchase history
    - Check total spend
    - Check VIP flag
    """
    # Stub implementation
    return "returning"  # Default to returning
    
    # Real implementation example:
    # user = await self._db.users.find_one({"_id": user_id})
    # if user.get("is_vip"): return "vip"
    # if user.get("lifetime_purchases") > 100000: return "bulk_buyer"
    # if user.get("purchase_count") == 0: return "new"
    # return "returning"
```

### Update Transaction Passport Integration

Add discount decision to passport entries:

```python
# In the negotiate node, after discount decision:

# Record in transaction passport
if hasattr(state, 'passport_id'):
    await self._passport_engine.add_entry(
        passport_id=state.passport_id,
        transaction_id=str(state.id),
        actor="POLICY_ENGINE",
        event_type="DISCOUNT_POLICY_DECISION",
        payload={
            "requested_discount_pct": proposed_discount_pct,
            "approved_discount_pct": discount_decision.approved_discount_pct,
            "policy_applied": discount_decision.policy_applied,
            "user_type": discount_request.user_type,
            "reason": discount_decision.reason
        },
        session_id=state.id,
        decision_id=uuid4()  # Generate decision_id
    )
```

---

## Step 4: API Endpoint for Policy Management

### Create Merchant Policy Configuration Endpoint

Create `api/routes/discount_policies.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal
from datetime import datetime

router = APIRouter(prefix="/api/v1/discount-policies", tags=["discount_policies"])

class SegmentLimit(BaseModel):
    user_segment: str  # "new", "returning", "vip", "bulk_buyer"
    max_discount_pct: Decimal = Field(..., ge=0, le=100)

class CreateDiscountPolicyRequest(BaseModel):
    product_sku: str
    max_discount_pct: Decimal = Field(..., ge=0, le=100)
    daily_budget_paise: int = Field(..., gt=0)
    weekly_budget_paise: int = Field(default=0, ge=0)
    segment_limits: Optional[List[SegmentLimit]] = None
    description: Optional[str] = None
    blacklist_combos: Optional[List[str]] = None

class DiscountPolicyResponse(BaseModel):
    policy_id: str
    merchant_id: str
    product_sku: str
    max_discount_pct: Decimal
    daily_budget_paise: int
    weekly_budget_paise: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

@router.post("/", response_model=DiscountPolicyResponse)
async def create_discount_policy(
    req: CreateDiscountPolicyRequest,
    merchant_id: str = Depends(get_current_merchant),
    db = Depends(get_db)
):
    """
    Create/update discount policy for a product.
    
    Only merchants can modify their own policies.
    """
    # Upsert policy
    policy = await db.execute("""
        INSERT INTO discount_policies 
        (merchant_id, product_sku, max_discount_pct, daily_budget_paise, weekly_budget_paise, description)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (merchant_id, product_sku)
        DO UPDATE SET
            max_discount_pct = EXCLUDED.max_discount_pct,
            daily_budget_paise = EXCLUDED.daily_budget_paise,
            weekly_budget_paise = EXCLUDED.weekly_budget_paise,
            description = EXCLUDED.description,
            updated_at = NOW()
        RETURNING *
    """, (merchant_id, req.product_sku, req.max_discount_pct, 
          req.daily_budget_paise, req.weekly_budget_paise, req.description))
    
    policy_id = policy["policy_id"]
    
    # Add segment overrides if provided
    if req.segment_limits:
        for segment in req.segment_limits:
            await db.execute("""
                INSERT INTO discount_segments 
                (policy_id, user_segment, max_discount_pct)
                VALUES (%s, %s, %s)
                ON CONFLICT (policy_id, user_segment)
                DO UPDATE SET max_discount_pct = EXCLUDED.max_discount_pct
            """, (policy_id, segment.user_segment, segment.max_discount_pct))
    
    return DiscountPolicyResponse(**policy)

@router.get("/{product_sku}", response_model=DiscountPolicyResponse)
async def get_discount_policy(
    product_sku: str,
    merchant_id: str = Depends(get_current_merchant),
    db = Depends(get_db)
):
    """Retrieve discount policy for a product."""
    policy = await db.fetch_one("""
        SELECT * FROM discount_policies
        WHERE merchant_id = %s AND product_sku = %s
    """, (merchant_id, product_sku))
    
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    
    return DiscountPolicyResponse(**policy)

@router.get("/analytics/{product_sku}")
async def get_policy_analytics(
    product_sku: str,
    merchant_id: str = Depends(get_current_merchant),
    db = Depends(get_db)
):
    """Get analytics on discount policy effectiveness."""
    analytics = await db.fetch_one("""
        SELECT * FROM discount_policy_analytics
        WHERE merchant_id = %s AND product_sku = %s
    """, (merchant_id, product_sku))
    
    return analytics or {}
```

### Register Route in `main.py`

```python
from api.routes.discount_policies import router as discount_router

app.include_router(discount_router)
```

---

## Step 5: Testing Discount Policy

### Unit Test Example

```python
# tests/test_discount_policy.py

import pytest
from decimal import Decimal
from api.policy.discount_policy import (
    DiscountPolicyEngine,
    DiscountPolicy,
    DiscountRequest,
    UserType
)

@pytest.fixture
def engine():
    return DiscountPolicyEngine()

@pytest.fixture
def sample_policy(engine):
    policy = DiscountPolicy(
        policy_id="policy_test_001",
        merchant_id="merchant_test",
        product_sku="TEST-SKU-001",
        max_discount_pct=25.0,
        daily_budget_paise=50000,
        per_user_type={
            UserType.VIP: 35.0,
            UserType.BULK_BUYER: 30.0
        }
    )
    engine.register_policy(policy)
    return policy

def test_discount_within_global_limit(engine, sample_policy):
    """Test: Discount within global limit is approved."""
    request = DiscountRequest(
        merchant_id="merchant_test",
        product_sku="TEST-SKU-001",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=15.0
    )
    
    decision = engine.check_discount_request(request)
    
    assert decision.approved == True
    assert decision.approved_discount_pct == 15.0
    assert decision.policy_applied == "WITHIN_LIMIT_returning"

def test_discount_reduced_by_user_type(engine, sample_policy):
    """Test: VIP gets higher limit than global."""
    request = DiscountRequest(
        merchant_id="merchant_test",
        product_sku="TEST-SKU-001",
        user_id="user_vip",
        user_type="vip",
        requested_discount_pct=40.0  # Request 40%
    )
    
    decision = engine.check_discount_request(request)
    
    assert decision.approved == True
    assert decision.approved_discount_pct == 35.0  # VIP max is 35%
    assert decision.policy_applied == "USER_TYPE_LIMIT_vip"

def test_discount_denied_by_budget(engine, sample_policy):
    """Test: Daily budget limits discount."""
    # Exhaust daily budget
    engine._daily_usage[sample_policy.policy_id] = 50000
    
    request = DiscountRequest(
        merchant_id="merchant_test",
        product_sku="TEST-SKU-001",
        user_id="user_123",
        user_type="returning",
        requested_discount_pct=15.0
    )
    
    decision = engine.check_discount_request(request)
    
    assert decision.approved == False
    assert decision.approved_discount_pct == 0.0
    assert decision.policy_applied == "DAILY_BUDGET_EXCEEDED"

def test_no_policy_denies_discount(engine):
    """Test: No policy = no discounts allowed."""
    request = DiscountRequest(
        merchant_id="merchant_unknown",
        product_sku="UNKNOWN-SKU",
        user_id="user_123",
        user_type="new",
        requested_discount_pct=10.0
    )
    
    decision = engine.check_discount_request(request)
    
    assert decision.approved == False
    assert decision.policy_applied == "NO_POLICY"
```

### Integration Test

```python
# tests/test_discount_integration.py

@pytest.mark.asyncio
async def test_negotiate_node_applies_discount_policy(session_service, db):
    """Test: Negotiate node enforces discount policy."""
    
    # Setup policy
    await db.execute("""
        INSERT INTO discount_policies (merchant_id, product_sku, max_discount_pct, daily_budget_paise)
        VALUES (%s, %s, %s, %s)
    """, ("merchant_keen", "TEST-SKU", 20.0, 100000))
    
    # Create session
    state = KeenPayState(
        id="session_test_001",
        merchant_id="merchant_keen",
        user_id="user_123",
        status="negotiating",
        selected_line_items=[{"sku": "TEST-SKU", "quantity": 1}],
        proposed_offer={"discount_pct": 50.0}  # AI proposes 50%
    )
    
    # Run negotiate
    result = await session_service.negotiate(state)
    
    # Should be reduced to policy max
    assert result.proposed_offer["discount_pct"] == 20.0
    assert result.proposed_offer["policy_applied"] == "GLOBAL_MAX_LIMIT"
```

---

## Step 6: Monitoring & Analytics

### Dashboard Queries

```sql
-- Policy effectiveness
SELECT product_sku, max_discount_pct, avg_approved_pct, approved_count, reduced_count, denied_count
FROM discount_policy_analytics
WHERE merchant_id = 'merchant_keen'
ORDER BY total_decisions DESC;

-- Daily budget consumption
SELECT tracking_date, daily_used_paise, daily_remaining
FROM discount_usage_tracking
WHERE policy_id = '{{policy_id}}'
ORDER BY tracking_date DESC
LIMIT 30;

-- Decisions denied due to budget
SELECT COUNT(*) as denied_count, SUM(requested_discount_pct) as total_requested_pct
FROM discount_decisions
WHERE policy_id = '{{policy_id}}'
AND status = 'DENIED'
AND created_at > NOW() - INTERVAL '7 days';
```

### Metrics to Track

- **approval_rate**: % of requests approved
- **avg_approved_discount_pct**: Average discount given
- **budget_exhaustion_days**: Days when daily budget hit 100%
- **requests_reduced**: Count where approved < requested
- **policy_violation_attempts**: Requests exceeding policy limit

---

## Example Workflow

### Merchant Setup

1. Create policy for product:
   ```bash
   curl -X POST http://localhost:8000/api/v1/discount-policies \
     -H "Authorization: Bearer merchant_token" \
     -H "Content-Type: application/json" \
     -d '{
       "product_sku": "HOODIE-NAVY-M",
       "max_discount_pct": 25,
       "daily_budget_paise": 50000,
       "segment_limits": [
         {"user_segment": "vip", "max_discount_pct": 35},
         {"user_segment": "bulk_buyer", "max_discount_pct": 30}
       ]
     }'
   ```

### Checkout Negotiation

1. User starts checkout
2. LangGraph triggers negotiate node
3. AI proposes 30% discount for VIP
4. Negotiate node calls `discount_engine.check_discount_request()`
5. Engine returns: approved 35% (VIP segment limit)
6. Decision recorded in `discount_decisions` table
7. Transaction passport records decision
8. User sees 35% discount (bounded by merchant policy)

### Audit Trail

```sql
-- View discount decision for session
SELECT decision_id, requested_discount_pct, approved_discount_pct, 
       policy_applied, decision_reason, created_at
FROM discount_decisions
WHERE session_id = 'session_abc'
ORDER BY created_at DESC;
```

---

## Summary

| Component | Purpose | File |
|-----------|---------|------|
| Schema | Database tables for policies | `docs/DISCOUNT_POLICY_SCHEMA.sql` |
| Engine | Bounded AI enforcement | `api/policy/discount_policy.py` |
| Session | Integration with negotiation | `api/services/session.py` |
| API | Merchant policy configuration | `api/routes/discount_policies.py` |
| Tests | Validation & compliance | `tests/test_discount_policy.py` |
| Audit | Immutable decision log | `discount_decisions` table |

**Result:** AI can no longer propose unbounded discounts. Every proposal is validated against merchant-defined bounds, recorded for audit, and passed to user with full transparency.

