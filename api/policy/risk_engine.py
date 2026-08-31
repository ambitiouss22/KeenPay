"""Risk Engine — Anomaly scoring and escalation logic (AegisPay pattern).

Separate from Policy Engine. Returns risk score (0-1) per transaction.
- 0.0-0.3: Low risk (proceed)
- 0.3-0.7: Medium risk (monitor)
- 0.7-1.0: High risk (escalate to human)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RiskLevel(str, Enum):
    """Risk categorization."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskScore:
    """Risk assessment result."""
    score: float  # 0.0 to 1.0
    level: RiskLevel
    signals: list[str]  # Individual risk signals detected
    recommendation: str  # "PROCEED", "MONITOR", "ESCALATE"
    metadata: dict


class RiskEngine:
    """Calculates transaction risk without invoking policy."""

    def __init__(self):
        """Initialize risk engine."""
        self.injection_weight = 0.5
        self.discount_weight = 0.3
        self.velocity_weight = 0.2
        self.anomaly_weight = 0.25

    def score_transaction(
        self,
        *,
        user_text: str,
        discount_pct: float,
        policy_max_discount: float,
        request_count_per_minute: int,
        previous_attempts_today: int,
        session_id: str,
    ) -> RiskScore:
        """Score a transaction for risk.

        Args:
            user_text: Raw user input
            discount_pct: Requested discount percentage
            policy_max_discount: Policy-defined max discount
            request_count_per_minute: Requests in last 60s
            previous_attempts_today: Failed attempts today
            session_id: Session identifier

        Returns:
            RiskScore with composite risk assessment
        """
        signals = []
        score_components = {}

        # Signal 1: Prompt injection indicators
        injection_score = self._score_injection(user_text)
        if injection_score > 0.1:
            signals.append(f"injection_detected (score={injection_score:.2f})")
        score_components["injection"] = injection_score

        # Signal 2: Discount anomaly
        discount_score = self._score_discount_anomaly(
            discount_pct, policy_max_discount
        )
        if discount_score > 0.1:
            signals.append(f"discount_anomaly (requested={discount_pct}%, max={policy_max_discount}%)")
        score_components["discount"] = discount_score

        # Signal 3: Velocity (rapid requests)
        velocity_score = self._score_velocity(request_count_per_minute)
        if velocity_score > 0.1:
            signals.append(f"high_velocity (requests_per_min={request_count_per_minute})")
        score_components["velocity"] = velocity_score

        # Signal 4: Repeated failed attempts
        anomaly_score = self._score_repeated_failures(previous_attempts_today)
        if anomaly_score > 0.1:
            signals.append(f"repeated_failures (attempts_today={previous_attempts_today})")
        score_components["anomaly"] = anomaly_score

        # Composite score (weighted average)
        total_score = (
            self.injection_weight * injection_score +
            self.discount_weight * discount_score +
            self.velocity_weight * velocity_score +
            self.anomaly_weight * anomaly_score
        )
        total_score = min(1.0, max(0.0, total_score))  # Clamp 0-1

        # Determine level and recommendation
        if total_score < 0.3:
            level = RiskLevel.LOW
            recommendation = "PROCEED"
        elif total_score < 0.65:
            level = RiskLevel.MEDIUM
            recommendation = "MONITOR"
        elif total_score < 0.85:
            level = RiskLevel.HIGH
            recommendation = "ESCALATE"
        else:
            level = RiskLevel.CRITICAL
            recommendation = "BLOCK"

        return RiskScore(
            score=total_score,
            level=level,
            signals=signals,
            recommendation=recommendation,
            metadata={
                "components": score_components,
                "session_id": session_id,
                "weights": {
                    "injection": self.injection_weight,
                    "discount": self.discount_weight,
                    "velocity": self.velocity_weight,
                    "anomaly": self.anomaly_weight,
                },
            },
        )

    def _score_injection(self, text: str) -> float:
        """Detect prompt injection patterns."""
        patterns = [
            r"(?i)ignore\s+(all\s+)?(previous|prior|my)",
            r"(?i)disregard\s+(policy|rules|guardrails)",
            r"(?i)system\s+prompt",
            r"(?i)bypass\s+(security|validation|policy)",
            r"(?i)admin\s+override",
            r"(?i)create\s+(a\s+)?payment.*(?:cheap|free|zero)",
        ]

        import re
        matches = sum(1 for p in patterns if re.search(p, text))
        return min(1.0, matches * 0.3)  # Each match adds 0.3

    def _score_discount_anomaly(self, requested: float, policy_max: float) -> float:
        """Score discount requests beyond policy."""
        if requested <= policy_max:
            return 0.0

        ratio = requested / policy_max if policy_max > 0 else 1.0
        if ratio <= 1.0:
            return 0.0
        elif ratio <= 1.5:
            return 0.2  # 50% over max
        elif ratio <= 2.0:
            return 0.5  # 2x max
        else:
            return 0.9  # 3x+ max

    def _score_velocity(self, requests_per_minute: int) -> float:
        """Score rapid-fire requests."""
        if requests_per_minute < 5:
            return 0.0
        elif requests_per_minute < 15:
            return 0.2
        elif requests_per_minute < 30:
            return 0.5
        else:
            return 0.9

    def _score_repeated_failures(self, attempts_today: int) -> float:
        """Score repeated failed attempts."""
        if attempts_today < 3:
            return 0.0
        elif attempts_today < 5:
            return 0.2
        elif attempts_today < 10:
            return 0.5
        else:
            return 0.9


# Singleton instance
risk_engine = RiskEngine()
