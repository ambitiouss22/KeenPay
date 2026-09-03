"""Risk scoring: how worried should we be about this particular action?

The second Phase 5 gate. Policy has already answered the categorical question -
*may* this happen at all - and this answers the graded one: how much scrutiny
does it deserve? The two are separate because they fail differently. A policy
denial is a rule being broken and no amount of context excuses it; a high risk
score is a pattern that is usually fine and occasionally fraud, and the right
response is a human, not a refusal.

Four signals, as narrow and explainable as they can be made:

``amount``      how large this is relative to what this merchant normally moves
``geography``   where the buyer is, and whether that agrees with where they are
``new_buyer``   account age and history - fraud arrives with no past
``velocity``    how much has already happened in the trailing hour

Every signal is a pure function of values passed in. Nothing here reads a
clock, a database or a request. That is what lets a score be recomputed from a
stored decision and come out the same, which is the only way to audit one.

The output is advisory to the authorization service, never a gate on its own.
A score that could block by itself would be a scoring bug away from refusing
every transaction in the country.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.policy import MerchantPolicy, load_merchant_policy
from policy.models import ActionKind, FinancialAction

#: Band edges. Below LOW_MAX is routine, below MEDIUM_MAX wants one pair of
#: eyes, above it wants a quorum. Named constants rather than literals buried
#: in a chain of ifs, because these are the numbers a risk team will argue
#: about and they should be findable.
LOW_MAX = 0.35
MEDIUM_MAX = 0.70

#: Equal-weight baseline. A weight above this amplifies its signal, below it
#: dampens - so the weights read as "relative emphasis", not as fractions that
#: must be mentally divided by their sum.
_BASELINE = 0.25


class RiskBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RiskAssessment:
    """What the scorer concluded, and why.

    Frozen: an assessment is evidence about a moment. Code that wanted to
    "adjust the score a bit" downstream would be quietly rewriting the record
    an auditor later reads.
    """

    score: float
    band: RiskBand
    signals: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_high(self) -> bool:
        return self.band is RiskBand.HIGH

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "band": self.band.value,
            "signals": list(self.signals),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "metadata": dict(self.metadata),
        }


class RiskService:
    """Scores a financial action from four independent signals."""

    def __init__(
        self,
        *,
        amount_weight: float = 0.30,
        geo_weight: float = 0.25,
        new_buyer_weight: float = 0.20,
        velocity_weight: float = 0.25,
    ) -> None:
        self.amount_weight = amount_weight
        self.geo_weight = geo_weight
        self.new_buyer_weight = new_buyer_weight
        self.velocity_weight = velocity_weight

    # --- public -------------------------------------------------------------

    def assess(
        self, action: FinancialAction, *, policy: MerchantPolicy | None = None
    ) -> RiskAssessment:
        """Score one action. Same inputs, same score, always."""
        policy = policy or load_merchant_policy(action.merchant_id)

        raw = {
            "amount": self._score_amount(action, policy),
            "geography": self._score_geography(action, policy),
            "new_buyer": self._score_new_buyer(action),
            "velocity": self._score_velocity(action, policy),
        }
        weights = {
            "amount": self.amount_weight,
            "geography": self.geo_weight,
            "new_buyer": self.new_buyer_weight,
            "velocity": self.velocity_weight,
        }
        adjusted = {
            name: min(1.0, value * weights[name] / _BASELINE) for name, value in raw.items()
        }

        score = self._combine(adjusted)
        band = self.band_for(score)
        signals = [self._describe(name, action, raw[name]) for name in raw if raw[name] > 0.0]

        return RiskAssessment(
            score=score,
            band=band,
            signals=signals,
            components=raw,
            metadata={
                "adjusted": {k: round(v, 4) for k, v in adjusted.items()},
                "weights": weights,
                "action_kind": action.kind.value,
                "policy_version": policy.policy_version,
            },
        )

    @staticmethod
    def band_for(score: float) -> RiskBand:
        if score < LOW_MAX:
            return RiskBand.LOW
        if score < MEDIUM_MAX:
            return RiskBand.MEDIUM
        return RiskBand.HIGH

    # --- aggregation --------------------------------------------------------

    @staticmethod
    def _combine(adjusted: dict[str, float]) -> float:
        """The strongest signal sets the floor; the rest escalate from there.

        A plain weighted average is the wrong shape for risk. It lets one
        severe signal be diluted by three quiet ones: a maxed-out geography
        flag averaged with three zeroes scores 0.25 and reads as "low", which
        is precisely backwards - a single unambiguous fraud indicator is not
        made safe by the transaction being unremarkable in other respects.

        So: take the strongest signal as the base, then climb through the
        remaining headroom in proportion to how much the others corroborate it.
        ``corr / (1 + corr)`` saturates, so additional signals always raise the
        score and can never on their own reach 1.0 - only a maxed-out dominant
        signal can. Weak evidence accumulating forever into certainty is the
        failure mode that produces false positives at scale.
        """
        if not adjusted:  # pragma: no cover - the signal set is fixed
            return 0.0
        base = max(adjusted.values())
        corroboration = sum(adjusted.values()) - base
        score = base + (1.0 - base) * (corroboration / (1.0 + corroboration))
        return min(1.0, max(0.0, score))

    # --- signals ------------------------------------------------------------

    @staticmethod
    def _reference_amount(action: FinancialAction, policy: MerchantPolicy) -> int:
        """What counts as "large" for this kind of action.

        The escalation threshold where one exists, the hard ceiling otherwise.
        Scoring every kind against one global number would make a ₹50,000
        payout look as ordinary as a ₹50,000 payment, when a payout is the one
        that empties an account.
        """
        thresholds = {
            ActionKind.PAYMENT: policy.escalate_payment_above_paise,
            ActionKind.REFUND: policy.escalate_refund_above_paise,
            ActionKind.PAYOUT: policy.max_payout_paise,
            ActionKind.CAMPAIGN_SPEND: policy.max_campaign_spend_paise,
        }
        return max(1, thresholds.get(action.kind, policy.max_payment_paise))

    def _score_amount(self, action: FinancialAction, policy: MerchantPolicy) -> float:
        """Ramp with size, relative to what is normal for this action kind.

        Half the reference amount is where interest starts. Below that the
        signal is zero rather than "a little bit": a scorer that returns a
        small positive number for every ordinary transaction adds a constant
        to every score and moves the band edges without anyone deciding to.
        """
        reference = self._reference_amount(action, policy)
        ratio = action.amount_paise / reference

        if ratio <= 0.5:
            return 0.0
        if ratio <= 1.0:
            return 0.2
        if ratio <= 2.0:
            # Just over the line. Enough for one pair of eyes, not for a
            # quorum: a cliff where one rupee more demands a second approver
            # trains people to split payments to stay under it.
            return 0.45
        if ratio <= 4.0:
            return 0.7
        return 0.9

    @staticmethod
    def _score_geography(action: FinancialAction, policy: MerchantPolicy) -> float:
        """Where they claim to be, and whether the network agrees.

        The mismatch matters more than the country. A buyer in an unusual
        market is a sale; a buyer whose stated country disagrees with the one
        their traffic arrives from is either a VPN or someone using a stolen
        card from somewhere they would rather not name.
        """
        buyer = (action.buyer_country or "").upper()
        ip = (action.ip_country or "").upper()
        blocked = {c.upper() for c in policy.blocked_countries}
        allowed = {c.upper() for c in policy.allowed_countries}

        if buyer in blocked or ip in blocked:
            return 1.0
        if buyer and ip and buyer != ip:
            # One pair of eyes on its own. A mismatch is often a traveller or a
            # VPN, and scoring it high enough to demand a quorum by itself
            # would put every remote worker's purchase into a manual queue.
            return 0.6
        if allowed and buyer and buyer not in allowed:
            return 0.5
        return 0.0

    @staticmethod
    def _score_new_buyer(action: FinancialAction) -> float:
        """Fraud has no history. Neither does a first-time customer.

        Which is why this signal is scored but never decisive on its own - the
        two are indistinguishable at this layer, and a system that refused
        every first purchase would have no second ones. It takes corroboration
        from another signal to push a new buyer into the high band.
        """
        age = action.buyer_age_days
        orders = action.buyer_prior_orders

        if age == 0 and orders == 0:
            return 0.8
        if age < 7 and orders == 0:
            return 0.5
        if age < 30 and orders < 2:
            return 0.3
        return 0.0

    @staticmethod
    def _score_velocity(action: FinancialAction, policy: MerchantPolicy) -> float:
        """How busy the trailing hour has been, against this merchant's limits.

        Scored against the same thresholds the policy rules use, so risk and
        policy cannot drift into disagreeing about what "fast" means.
        """
        count = action.actions_last_hour
        escalate_at = max(1, policy.escalate_actions_per_hour_above)
        hard_at = max(escalate_at + 1, policy.max_actions_per_hour)

        if count < escalate_at / 2:
            return 0.0
        if count < escalate_at:
            return 0.3
        if count < hard_at:
            return 0.7
        return 1.0

    # --- explanation --------------------------------------------------------

    @staticmethod
    def _describe(name: str, action: FinancialAction, value: float) -> str:
        detail = {
            "amount": f"amount={action.amount_paise} paise",
            "geography": f"buyer={action.buyer_country} ip={action.ip_country}",
            "new_buyer": (
                f"age_days={action.buyer_age_days} prior_orders={action.buyer_prior_orders}"
            ),
            "velocity": f"actions_last_hour={action.actions_last_hour}",
        }[name]
        return f"{name} ({detail}, signal={value:.2f})"


#: Shared instance. Stateless, so one is enough.
risk_service = RiskService()


__all__ = ["LOW_MAX", "MEDIUM_MAX", "RiskAssessment", "RiskBand", "RiskService", "risk_service"]
