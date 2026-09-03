"""Refund eligibility: may this money go back, and how much of it?

A refund is the money path with the least natural friction and the most ways to
go wrong. A payment fails loudly when a card declines; a refund succeeds
quietly, and the mistake is discovered when the books are reconciled a month
later. So the checks here are arithmetic rather than judgement, and every one
of them is a subtraction someone has got wrong in production somewhere:

* refunding more than was captured,
* refunding the full amount twice because earlier refunds were not deducted,
* refunding an order that was never paid,
* refunding in floats and losing a paisa per transaction,
* refunding years later, after the acquirer's window has closed and the money
  leaves the merchant without ever reaching the cardholder.

The guard decides *eligibility* and *how much*. It does not decide whether the
refund is allowed to happen - that is the authorization gate's answer, and this
module hands it the action to judge rather than judging it here. Keeping those
apart is what stops a "small refunds are fine" shortcut from growing into a
second, weaker gate beside the real one.

Everything is a pure function of its arguments, ``now`` included. A refund
verdict has to be reproducible: "was this eligible at the time?" is a question
that gets asked during chargeback disputes, long after the clock has moved on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from config.policy import MerchantPolicy, load_merchant_policy
from core.exceptions import ValidationError
from policy.models import ActionKind, FinancialAction

#: Order statuses money can come back from. An allow-list: a status nobody
#: anticipated must not be refundable by default.
REFUNDABLE_STATUSES = frozenset({"paid", "captured", "partially_refunded"})


@dataclass(frozen=True)
class RefundVerdict:
    """Whether a refund may proceed, and the ceiling on it."""

    eligible: bool
    max_refundable_paise: int
    reasons: list[str] = field(default_factory=list)
    #: True when the amount is large enough that the authorization gate will
    #: certainly demand a human. Advisory - the gate decides for itself.
    requires_authorization: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "max_refundable_paise": self.max_refundable_paise,
            "reasons": list(self.reasons),
            "requires_authorization": self.requires_authorization,
            "details": dict(self.details),
        }


def _paid_at(order: dict[str, Any]) -> datetime | None:
    value = order.get("paid_at")
    if value is None:
        return None
    if isinstance(value, str):  # pragma: no cover - Postgres returns datetimes
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def refundable_paise(order: dict[str, Any]) -> int:
    """What is left to give back.

    Captured minus already refunded, floored at zero. The floor matters: a
    negative "refundable" would compare as less than any requested amount and
    read as a failure for the wrong reason, hiding the real problem, which is
    that the books already do not balance.
    """
    captured = int(order.get("final_amount_paise") or 0)
    already = int(order.get("refunded_paise") or 0)
    return max(0, captured - already)


class RefundGuard:
    """Eligibility rules for refunds."""

    def __init__(self, *, policy: MerchantPolicy | None = None) -> None:
        self._policy = policy

    def _resolve_policy(self, merchant_id: str) -> MerchantPolicy:
        return self._policy or load_merchant_policy(merchant_id)

    def evaluate(
        self,
        *,
        order: dict[str, Any] | None,
        merchant_id: str,
        amount_paise: Any,
        now: datetime | None = None,
    ) -> RefundVerdict:
        """Judge one proposed refund.

        Collects every reason rather than stopping at the first. A refund
        refused for four reasons that reports one sends an operator round the
        loop four times.
        """
        now = now or datetime.now(UTC)
        policy = self._resolve_policy(merchant_id)
        reasons: list[str] = []

        if order is None:
            # No order means nothing to reason about - the remaining rules
            # would all be arithmetic on zeroes and would report misleading
            # secondary failures.
            return RefundVerdict(
                eligible=False,
                max_refundable_paise=0,
                reasons=["order not found"],
                details={"merchant_id": merchant_id},
            )

        if order.get("merchant_id") != merchant_id:
            # Same answer as a missing order, and deliberately no detail about
            # the real owner: confirming that an id belongs to *someone* is
            # how an attacker maps another merchant's order numbers.
            return RefundVerdict(
                eligible=False,
                max_refundable_paise=0,
                reasons=["order not found"],
                details={"merchant_id": merchant_id},
            )

        # Amount first, and strictly. A float here is the bug that silently
        # refunds 249 paise instead of 249.9 - and unlike a price, nobody
        # notices, because the customer got money back.
        if isinstance(amount_paise, bool) or not isinstance(amount_paise, int):
            raise ValidationError(
                "INVALID_AMOUNT",
                "refund amount must be an integer number of paise, "
                f"got {type(amount_paise).__name__}",
                {"field": "amount_paise", "value": repr(amount_paise)},
            )
        if amount_paise <= 0:
            reasons.append("refund amount must be greater than zero")

        status = str(order.get("status") or "")
        if status not in REFUNDABLE_STATUSES:
            reasons.append(f"order status {status!r} is not refundable")

        remaining = refundable_paise(order)
        if remaining <= 0:
            reasons.append("this order has already been fully refunded")
        elif amount_paise > remaining:
            reasons.append(
                f"refund of {amount_paise} paise exceeds the {remaining} paise "
                "still refundable on this order"
            )

        paid_at = _paid_at(order)
        window = timedelta(days=policy.refund_window_days)
        if paid_at is None:
            if status in REFUNDABLE_STATUSES:
                # A paid order with no capture timestamp cannot be aged, and
                # an unageable order cannot be shown to be inside the window.
                # Failing closed is the only safe reading.
                reasons.append("order has no capture timestamp; refund window cannot be checked")
        elif now - paid_at > window:
            reasons.append(
                f"the {policy.refund_window_days}-day refund window closed on "
                f"{(paid_at + window).date().isoformat()}"
            )

        eligible = not reasons
        return RefundVerdict(
            eligible=eligible,
            max_refundable_paise=remaining,
            reasons=reasons,
            requires_authorization=(
                eligible and amount_paise > policy.escalate_refund_above_paise
            ),
            details={
                "order_id": order.get("id"),
                "status": status,
                "captured_paise": int(order.get("final_amount_paise") or 0),
                "already_refunded_paise": int(order.get("refunded_paise") or 0),
                "refund_window_days": policy.refund_window_days,
            },
        )

    @staticmethod
    def to_action(
        *,
        order: dict[str, Any],
        merchant_id: str,
        amount_paise: int,
        actor_id: str,
        actor_role: str,
        actions_last_hour: int = 0,
        today_total_paise: int = 0,
    ) -> FinancialAction:
        """Build the action the authorization gate will judge.

        The captured and already-refunded figures are read from the order here
        rather than accepted from the caller. A caller that could name what was
        captured could name a larger number and refund against it.
        """
        return FinancialAction(
            kind=ActionKind.REFUND,
            merchant_id=merchant_id,
            amount_paise=amount_paise,
            subject_id=str(order.get("id") or ""),
            actor_id=actor_id,
            actor_role=actor_role,
            captured_paise=int(order.get("final_amount_paise") or 0),
            already_refunded_paise=int(order.get("refunded_paise") or 0),
            actions_last_hour=actions_last_hour,
            today_total_paise=today_total_paise,
        )


#: Shared instance. Holds no state beyond an optional policy override.
refund_guard = RefundGuard()


__all__ = [
    "REFUNDABLE_STATUSES",
    "RefundGuard",
    "RefundVerdict",
    "refund_guard",
    "refundable_paise",
]
