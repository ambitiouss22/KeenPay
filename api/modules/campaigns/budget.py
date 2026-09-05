"""Campaign budget arithmetic.

Pure functions and one frozen value object. No I/O, no clock reads beyond the
``now`` a caller passes in, so every rule here is testable without a database
and holds identically for the HTTP path, the agent path and any future job.

Three numbers describe a campaign, and every question about it is answered from
them::

    remaining = budget - reserved - spent

``reserved`` is money promised but not yet taken; ``spent`` is money gone. They
are tracked apart because a released reservation must return to the pool while a
settled spend must not. Collapsing them into one counter is the shape that
either leaks budget on every abandoned checkout or lets a settled discount be
handed out twice.

Two rules the module is built around, both inherited from the commerce safety
layer for the same reasons:

**Money is integer paise.** A percentage cap is the only non-integer input here,
and it is carried as ``Decimal`` and floored, never as ``float``. A discount
computed in binary floating point reconciles to a different number than the one
charged.

**Reject, do not clamp.** A reservation larger than the remaining budget is
refused outright rather than trimmed to what is left. Trimming would hand back a
smaller discount than the caller asked for while reporting success, and the
caller would apply the number it asked for.

The cap enforced here is the *first* of two. This module refuses arithmetic that
would breach it; the ``campaigns_budget_not_exceeded`` check constraint in the
database refuses the write itself. A caller that bypassed this module entirely
still cannot overspend, which is the property that makes the cap hard rather
than merely intended.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from core.exceptions import ConflictError, ValidationError

#: Ceiling on any single campaign budget: 50,00,000 rupees. Not a business rule
#: so much as a guard - a budget with an extra three zeroes is a typo or an
#: attack, never a marketing plan, and it is far cheaper to refuse one than to
#: discover it after the discounts have gone out.
MAX_CAMPAIGN_BUDGET_PAISE = 500_000_000

#: Ceiling on one reservation. Deliberately the same figure as the order total
#: cap in ``modules.commerce.safety``: a reservation funds a discount on one
#: order, so it can never legitimately exceed what one order may cost.
MAX_RESERVATION_PAISE = 500_000_000


class BudgetExceededError(ConflictError):
    """The reservation would breach the campaign's cap.

    409 rather than 422: the request was well formed and would have been
    accepted a moment earlier. Nothing about it is malformed - there is simply
    no money left.
    """

    code = "BUDGET_EXCEEDED"


class CampaignInactiveError(ConflictError):
    """The campaign is switched off, not yet started, or finished."""

    code = "CAMPAIGN_INACTIVE"


def _reject_non_integer(value: Any, field: str) -> int:
    """Accept only a true ``int``.

    ``bool`` is refused despite subclassing ``int``: ``True == 1`` in Python, so
    a stray boolean would otherwise become a reservation of one paisa. A float
    is refused rather than rounded, because rounding is the silent corruption
    the paise convention exists to prevent.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "INVALID_AMOUNT",
            f"{field} must be an integer number of paise, got {type(value).__name__}",
            {"field": field, "value": repr(value)},
        )
    return value


def validate_budget_paise(value: Any, *, field: str = "budget_paise") -> int:
    """A campaign budget must be a positive, sane whole number of paise."""
    paise = _reject_non_integer(value, field)
    if paise < 1:
        raise ValidationError(
            "INVALID_BUDGET",
            f"{field} must be at least 1 paisa, got {paise}",
            {"field": field, "value": paise},
        )
    if paise > MAX_CAMPAIGN_BUDGET_PAISE:
        raise ValidationError(
            "BUDGET_TOO_LARGE",
            f"{field} may not exceed {MAX_CAMPAIGN_BUDGET_PAISE} paise, got {paise}",
            {"field": field, "value": paise, "max": MAX_CAMPAIGN_BUDGET_PAISE},
        )
    return paise


def validate_reservation_paise(value: Any, *, field: str = "amount_paise") -> int:
    """A reservation must be strictly positive.

    Zero is refused as well as negative. A zero reservation is meaningless, and
    accepting it would put a no-op entry in an append-only ledger that exists to
    be read as a record of what happened. A negative one is an attempt to
    *create* budget by reserving backwards.
    """
    paise = _reject_non_integer(value, field)
    if paise < 1:
        raise ValidationError(
            "INVALID_RESERVATION",
            f"{field} must be at least 1 paisa, got {paise}",
            {"field": field, "value": paise},
        )
    if paise > MAX_RESERVATION_PAISE:
        raise ValidationError(
            "RESERVATION_TOO_LARGE",
            f"{field} may not exceed {MAX_RESERVATION_PAISE} paise, got {paise}",
            {"field": field, "value": paise, "max": MAX_RESERVATION_PAISE},
        )
    return paise


def normalise_discount_pct(value: Any, *, field: str = "max_discount_pct") -> Decimal | None:
    """Carry a percentage cap as an exact ``Decimal`` in ``[0, 100]``.

    ``None`` means the campaign sets no per-order percentage ceiling; the budget
    itself is still the hard limit. A float is accepted here - unlike money -
    because a percentage arrives from JSON as one and has no exact integer
    spelling; it is converted through ``str`` so ``12.5`` becomes ``Decimal("12.5")``
    rather than the binary approximation ``float`` actually holds.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError(
            "INVALID_DISCOUNT_PCT", f"{field} must be a number", {"field": field}
        )
    try:
        pct = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(
            "INVALID_DISCOUNT_PCT",
            f"{field} must be a number, got {value!r}",
            {"field": field, "value": repr(value)},
        ) from exc
    if pct.is_nan() or pct < 0 or pct > 100:
        raise ValidationError(
            "INVALID_DISCOUNT_PCT",
            f"{field} must be between 0 and 100, got {pct}",
            {"field": field, "value": str(pct)},
        )
    return pct


@dataclass(frozen=True)
class BudgetState:
    """The three counters, and everything derivable from them.

    Frozen because a budget snapshot is an observation, not a handle. Code that
    holds one and mutates it is code that believes it has the live figure; the
    live figure lives in the database, and the whole point of the reservation
    path is that only the database decides whether a spend fits.
    """

    budget_paise: int
    reserved_paise: int
    spent_paise: int

    @property
    def committed_paise(self) -> int:
        """Reserved plus spent: everything no longer available."""
        return self.reserved_paise + self.spent_paise

    @property
    def remaining_paise(self) -> int:
        """What is still reservable. Never negative for a consistent row."""
        return self.budget_paise - self.committed_paise

    @property
    def exhausted(self) -> bool:
        return self.remaining_paise <= 0

    def fits(self, amount_paise: int) -> bool:
        return amount_paise <= self.remaining_paise

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_paise": self.budget_paise,
            "reserved_paise": self.reserved_paise,
            "spent_paise": self.spent_paise,
            "remaining_paise": self.remaining_paise,
            "exhausted": self.exhausted,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BudgetState:
        return cls(
            budget_paise=int(row["budget_paise"]),
            reserved_paise=int(row.get("reserved_paise") or 0),
            spent_paise=int(row.get("spent_paise") or 0),
        )


def assert_reservable(state: BudgetState, amount_paise: int, *, campaign_id: str = "") -> int:
    """Refuse a reservation that would breach the cap.

    Advisory only, and deliberately so. It produces a precise error for a
    caller that asked for too much, but it is *not* what makes overspend
    impossible - between this check and the write, another request may take the
    last rupee. The single-statement UPDATE in the repository and the database
    check constraint are what actually hold. This exists so the common case
    fails with a useful message instead of a bare conflict.
    """
    amount = validate_reservation_paise(amount_paise)
    if not state.fits(amount):
        raise BudgetExceededError(
            "BUDGET_EXCEEDED",
            f"campaign has {state.remaining_paise} paise remaining, "
            f"cannot reserve {amount}",
            {
                "campaign_id": campaign_id,
                "requested_paise": amount,
                **state.to_dict(),
            },
        )
    return amount


def assert_releasable(state: BudgetState, amount_paise: int, *, campaign_id: str = "") -> int:
    """Refuse a release larger than what is currently reserved.

    Releasing more than was reserved would manufacture budget out of nothing:
    ``reserved`` floors at zero, so the excess reappears as headroom that was
    never funded.
    """
    amount = validate_reservation_paise(amount_paise)
    if amount > state.reserved_paise:
        raise ConflictError(
            "RELEASE_EXCEEDS_RESERVED",
            f"cannot release {amount} paise: only {state.reserved_paise} is reserved",
            {
                "campaign_id": campaign_id,
                "requested_paise": amount,
                **state.to_dict(),
            },
        )
    return amount


def assert_campaign_spendable(row: dict[str, Any], *, now: datetime | None = None) -> None:
    """Refuse a campaign that is off, not yet open, or already closed.

    Checked here as well as in the repository's UPDATE. The UPDATE's ``AND active``
    clause is the enforcement; this turns "zero rows matched" into an error that
    says which of the three reasons applied, which is the difference between an
    operator fixing a date and an operator filing a bug.
    """
    moment = now or datetime.now(UTC)
    campaign_id = str(row.get("id", ""))

    if not row.get("active", False):
        raise CampaignInactiveError(
            "CAMPAIGN_INACTIVE",
            "campaign is not active",
            {"campaign_id": campaign_id},
        )

    starts_at = _as_aware(row.get("starts_at"))
    if starts_at is not None and moment < starts_at:
        raise CampaignInactiveError(
            "CAMPAIGN_NOT_STARTED",
            f"campaign starts at {starts_at.isoformat()}",
            {"campaign_id": campaign_id, "starts_at": starts_at.isoformat()},
        )

    ends_at = _as_aware(row.get("ends_at"))
    if ends_at is not None and moment > ends_at:
        raise CampaignInactiveError(
            "CAMPAIGN_ENDED",
            f"campaign ended at {ends_at.isoformat()}",
            {"campaign_id": campaign_id, "ends_at": ends_at.isoformat()},
        )


def max_discount_paise(
    state: BudgetState, *, subtotal_paise: int, max_discount_pct: Any = None
) -> int:
    """The largest discount this campaign may fund for one order.

    The lower of two ceilings: what is left in the budget, and the campaign's
    per-order percentage cap applied to the subtotal. The percentage is floored,
    not rounded - rounding up would let a campaign fund a paisa more than its own
    stated cap, which is a small enough breach to go unnoticed and a real one.
    """
    subtotal = _reject_non_integer(subtotal_paise, "subtotal_paise")
    if subtotal < 0:
        raise ValidationError(
            "INVALID_AMOUNT",
            f"subtotal_paise may not be negative, got {subtotal}",
            {"subtotal_paise": subtotal},
        )

    ceiling = max(state.remaining_paise, 0)
    pct = normalise_discount_pct(max_discount_pct)
    if pct is not None:
        by_pct = (Decimal(subtotal) * pct / Decimal(100)).to_integral_value(
            rounding=ROUND_DOWN
        )
        ceiling = min(ceiling, int(by_pct))
    return min(ceiling, subtotal)


def _as_aware(value: Any) -> datetime | None:
    """Normalise a stored timestamp to an aware UTC datetime.

    A naive datetime compared against an aware one raises, and the stored value
    can be either depending on whether it came from Postgres or the in-memory
    store. Assuming UTC for a naive value matches how every other timestamp in
    the system is written.
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "MAX_CAMPAIGN_BUDGET_PAISE",
    "MAX_RESERVATION_PAISE",
    "BudgetExceededError",
    "BudgetState",
    "CampaignInactiveError",
    "assert_campaign_spendable",
    "assert_releasable",
    "assert_reservable",
    "max_discount_paise",
    "normalise_discount_pct",
    "validate_budget_paise",
    "validate_reservation_paise",
]
