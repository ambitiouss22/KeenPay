"""Checkout Guardian — Orchestrates Risk, Authorization, Passport (AegisPay pattern).

This is the integration layer that wires three engines:
1. Risk Engine: Detects anomalies early
2. Authorization Engine: Gates the payment with scoped, immutable authority
3. Transaction Passport: Records every decision for audit trail

The Guardian ensures every payment path is:
- BOUNDED: risk scored, amount authorized, limits checked
- GATED: single-use auth, cart-hash bound, temporal expiry
- EXPLAINABLE: every step recorded in passport
"""

from dataclasses import dataclass

from audit.transaction_passport import PassportEngine, TransactionPassport
from policy.authorization_engine import Authorization, AuthorizationEngine
from policy.risk_engine import RiskEngine, RiskScore


@dataclass
class GuardianCheckpoint:
    """Result of a checkout guardian check."""

    passed: bool
    risk_score: RiskScore | None = None
    authorization: Authorization | None = None
    passport: TransactionPassport | None = None
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class CheckoutGuardian:
    """Orchestrates the three-engine checkout flow."""

    def __init__(
        self,
        risk_engine: RiskEngine,
        authorization_engine: AuthorizationEngine,
        passport_engine: PassportEngine,
    ):
        """Initialize guardian with the three engines.

        Args:
            risk_engine: RiskEngine instance
            authorization_engine: AuthorizationEngine instance
            passport_engine: PassportEngine instance
        """
        self.risk = risk_engine
        self.auth = authorization_engine
        self.passport = passport_engine

    def guard_checkout(
        self,
        *,
        session_id: str,
        merchant_id: str,
        user_id: str | None,
        user_text: str,
        cart_items: list[dict],
        amount_paise: int,
        currency: str = "INR",
        policy_max_discount: float = 15.0,
        request_count_per_minute: int = 1,
        previous_attempts_today: int = 0,
    ) -> GuardianCheckpoint:
        """Run the full guardian checkpoint on a checkout.

        This method:
        1. Scores risk (anomaly detection)
        2. Creates payment authorization (if risk is acceptable)
        3. Records everything in transaction passport

        Args:
            session_id: Checkout session ID
            merchant_id: Merchant ID
            user_id: User ID (if authenticated)
            user_text: Raw user input
            cart_items: List of items (with SKU, qty, price)
            amount_paise: Final amount in paise
            currency: Currency code
            policy_max_discount: Policy-defined max discount %
            request_count_per_minute: Requests in last 60s
            previous_attempts_today: Failed attempts today

        Returns:
            GuardianCheckpoint with results
        """
        checkpoint = GuardianCheckpoint(passed=False)

        # Step 1: Create transaction passport
        order_id = session_id[:8]  # Temp ID; real order_id comes after payment
        passport = self.passport.create_passport(
            transaction_id=order_id,
            merchant_id=merchant_id,
        )
        checkpoint.passport = passport

        # Record: Checkout started
        passport.add_entry(
            actor="SYSTEM",
            event_type="CHECKOUT_STARTED",
            payload={
                "cart_items_count": len(cart_items),
                "amount_paise": amount_paise,
                "currency": currency,
            },
            session_id=session_id,
        )

        # Step 2: Risk scoring
        try:
            risk_score = self.risk.score_transaction(
                user_text=user_text,
                discount_pct=0.0,  # Will be refined per offer
                policy_max_discount=policy_max_discount,
                request_count_per_minute=request_count_per_minute,
                previous_attempts_today=previous_attempts_today,
                session_id=session_id,
            )
            checkpoint.risk_score = risk_score

            # Record risk assessment
            passport.add_entry(
                actor="SYSTEM",
                event_type="RISK_ASSESSED",
                payload={
                    "score": risk_score.score,
                    "level": risk_score.level.value,
                    "recommendation": risk_score.recommendation,
                    "signals": risk_score.signals,
                },
                session_id=session_id,
            )

            # Check risk level
            if risk_score.recommendation == "BLOCK":
                checkpoint.errors.append(f"High-risk transaction blocked: {risk_score.signals}")
                passport.add_entry(
                    actor="SYSTEM",
                    event_type="CHECKPOINT_BLOCKED",
                    payload={
                        "reason": "high_risk",
                        "risk_level": risk_score.level.value,
                    },
                    session_id=session_id,
                )
                return checkpoint

            if risk_score.recommendation == "ESCALATE":
                checkpoint.errors.append(f"Transaction escalated for review: {risk_score.signals}")
                passport.add_entry(
                    actor="SYSTEM",
                    event_type="CHECKPOINT_ESCALATED",
                    payload={
                        "reason": "medium_risk",
                        "risk_level": risk_score.level.value,
                    },
                    session_id=session_id,
                )
                # Don't return; allow escalation to proceed with human approval

        except Exception as e:
            checkpoint.errors.append(f"Risk scoring failed: {str(e)}")
            passport.add_entry(
                actor="SYSTEM",
                event_type="RISK_ASSESSMENT_ERROR",
                payload={"error": str(e)},
                session_id=session_id,
            )
            return checkpoint

        # Step 3: Create authorization
        try:
            auth = self.auth.create_authorization(
                session_id=session_id,
                merchant_id=merchant_id,
                cart_items=cart_items,
                amount_paise=amount_paise,
                currency=currency,
            )
            checkpoint.authorization = auth

            # Record authorization created
            passport.add_entry(
                actor="SYSTEM",
                event_type="AUTHORIZATION_CREATED",
                payload={
                    "auth_id": auth.auth_id,
                    "amount_paise": auth.amount_paise,
                    "cart_hash": auth.cart_hash,
                    "ttl_seconds": auth.metadata.get("ttl_seconds"),
                },
                session_id=session_id,
                auth_id=auth.auth_id,
            )

        except Exception as e:
            checkpoint.errors.append(f"Authorization creation failed: {str(e)}")
            passport.add_entry(
                actor="SYSTEM",
                event_type="AUTHORIZATION_ERROR",
                payload={"error": str(e)},
                session_id=session_id,
            )
            return checkpoint

        # Step 4: Success
        checkpoint.passed = True
        passport.add_entry(
            actor="SYSTEM",
            event_type="CHECKPOINT_PASSED",
            payload={
                "auth_id": auth.auth_id if checkpoint.authorization else None,
                "risk_level": risk_score.level.value if checkpoint.risk_score else None,
            },
            session_id=session_id,
        )

        # Verify passport integrity
        is_valid, verify_errors = passport.verify()
        if not is_valid:
            checkpoint.errors.extend(verify_errors)
            checkpoint.passed = False

        return checkpoint


def build_guardian(
    risk_engine: RiskEngine | None = None,
    auth_engine: AuthorizationEngine | None = None,
    passport_engine: PassportEngine | None = None,
) -> CheckoutGuardian:
    """Factory function to build a CheckoutGuardian with default engines.

    Args:
        risk_engine: Custom RiskEngine (default: new instance)
        auth_engine: Custom AuthorizationEngine (default: new instance)
        passport_engine: Custom PassportEngine (default: new instance)

    Returns:
        CheckoutGuardian ready to use
    """
    if risk_engine is None:
        risk_engine = RiskEngine()
    if auth_engine is None:
        auth_engine = AuthorizationEngine()
    if passport_engine is None:
        passport_engine = PassportEngine()

    return CheckoutGuardian(risk_engine, auth_engine, passport_engine)
