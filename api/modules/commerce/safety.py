"""Money and quantity invariants for the commerce path.

Pure functions, no I/O. Everything here is the kind of check that is cheap to
run on every write and expensive to discover missing in production.

Two rules the whole module is built around:

**Money is integer paise. Always.** Never a float, never rupees. ``0.1 + 0.2``
is not ``0.3`` in binary floating point, and a discount computed in floats
reconciles to a different number than the one charged. Storing paise as ``int``
makes every total exact, and rejecting floats at the boundary stops one from
leaking in from JSON and quietly poisoning arithmetic downstream.

**Reject, do not clamp.** A negative quantity is not "probably zero" and a
price of ten million rupees is not "probably a typo to round down". Silently
correcting nonsense hides the bug that produced it; refusing surfaces it while
someone can still fix it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from core.exceptions import ValidationError

#: One line may not exceed this quantity. Not a business rule so much as a
#: guard: a 2-billion quantity is a bug or an attack, never an order.
MAX_LINE_QUANTITY = 1_000
#: Distinct lines in one cart.
MAX_CART_LINES = 100
#: ₹10,00,000 per unit. Well above any real catalogue price, low enough that an
#: overflowed or garbage value cannot become a plausible charge.
MAX_UNIT_PRICE_PAISE = 100_000_000
#: ₹50,00,000 per order.
MAX_ORDER_TOTAL_PAISE = 500_000_000


def _reject_non_integer(value: Any, field: str) -> int:
    """Accept only a true ``int``.

    ``bool`` is explicitly refused even though it subclasses ``int``: Python
    evaluates ``True == 1``, so a stray boolean would sail through an ``isinstance``
    check and become a quantity of one. A float is refused rather than rounded,
    because rounding is precisely the silent corruption this module exists to
    prevent.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "INVALID_AMOUNT",
            f"{field} must be an integer number of paise, got {type(value).__name__}",
            {"field": field, "value": repr(value)},
        )
    return value


def validate_quantity(quantity: Any, *, field: str = "quantity") -> int:
    """A quantity must be a whole number, at least one, and sane."""
    qty = _reject_non_integer(quantity, field)
    if qty < 1:
        raise ValidationError(
            "INVALID_QUANTITY",
            f"{field} must be at least 1, got {qty}",
            {"field": field, "value": qty},
        )
    if qty > MAX_LINE_QUANTITY:
        raise ValidationError(
            "QUANTITY_TOO_LARGE",
            f"{field} may not exceed {MAX_LINE_QUANTITY}, got {qty}",
            {"field": field, "value": qty, "max": MAX_LINE_QUANTITY},
        )
    return qty


def validate_price_paise(price: Any, *, field: str = "price_paise") -> int:
    """A price must be a non-negative whole number of paise."""
    paise = _reject_non_integer(price, field)
    if paise < 0:
        raise ValidationError(
            "INVALID_PRICE",
            f"{field} may not be negative, got {paise}",
            {"field": field, "value": paise},
        )
    if paise > MAX_UNIT_PRICE_PAISE:
        raise ValidationError(
            "PRICE_TOO_LARGE",
            f"{field} may not exceed {MAX_UNIT_PRICE_PAISE} paise, got {paise}",
            {"field": field, "value": paise, "max": MAX_UNIT_PRICE_PAISE},
        )
    return paise


def line_total_paise(unit_price_paise: Any, quantity: Any) -> int:
    """Exact total for one line, both operands validated first."""
    unit = validate_price_paise(unit_price_paise, field="unit_price_paise")
    qty = validate_quantity(quantity)
    total = unit * qty
    if total > MAX_ORDER_TOTAL_PAISE:
        raise ValidationError(
            "LINE_TOTAL_TOO_LARGE",
            f"line total {total} exceeds {MAX_ORDER_TOTAL_PAISE} paise",
            {"unit_price_paise": unit, "quantity": qty, "line_total_paise": total},
        )
    return total


def cart_subtotal_paise(items: Iterable[Mapping[str, Any]]) -> int:
    """Sum of every line, in paise.

    Recomputed from the items rather than trusting any stored total. A total
    that travelled through a client is an input, not a fact.
    """
    lines = list(items)
    if len(lines) > MAX_CART_LINES:
        raise ValidationError(
            "TOO_MANY_LINES",
            f"a cart may hold at most {MAX_CART_LINES} lines, got {len(lines)}",
            {"lines": len(lines), "max": MAX_CART_LINES},
        )

    subtotal = 0
    for i, item in enumerate(lines):
        subtotal += line_total_paise(
            item.get("unit_price_paise"), item.get("quantity")
        )
        if subtotal > MAX_ORDER_TOTAL_PAISE:
            raise ValidationError(
                "ORDER_TOTAL_TOO_LARGE",
                f"cart total exceeds {MAX_ORDER_TOTAL_PAISE} paise at line {i + 1}",
                {"subtotal_paise": subtotal, "max": MAX_ORDER_TOTAL_PAISE},
            )
    return subtotal


def validate_discount_paise(discount_paise: Any, subtotal_paise: int) -> int:
    """A discount may not be negative, nor exceed what is being discounted.

    A discount larger than the subtotal produces a negative total - which, sent
    to a payment provider, is a refund. Worth one comparison to make impossible.
    """
    discount = _reject_non_integer(discount_paise, "discount_paise")
    if discount < 0:
        raise ValidationError(
            "INVALID_DISCOUNT",
            f"discount may not be negative, got {discount}",
            {"discount_paise": discount},
        )
    if discount > subtotal_paise:
        raise ValidationError(
            "DISCOUNT_EXCEEDS_SUBTOTAL",
            f"discount {discount} exceeds subtotal {subtotal_paise}",
            {"discount_paise": discount, "subtotal_paise": subtotal_paise},
        )
    return discount


def final_total_paise(subtotal_paise: int, discount_paise: int = 0) -> int:
    """Subtotal minus discount, guaranteed to be >= 0."""
    discount = validate_discount_paise(discount_paise, subtotal_paise)
    return subtotal_paise - discount


def assert_totals_reconcile(
    items: Iterable[Mapping[str, Any]],
    *,
    claimed_subtotal_paise: int,
    claimed_discount_paise: int = 0,
    claimed_total_paise: int | None = None,
) -> None:
    """Recompute the arithmetic and refuse if a claimed figure disagrees.

    The check that matters before charging anyone. A client that can name its
    own total can name a smaller one; recomputing from line items and comparing
    turns that into a rejected request instead of an underpayment.
    """
    subtotal = cart_subtotal_paise(items)
    if subtotal != claimed_subtotal_paise:
        raise ValidationError(
            "SUBTOTAL_MISMATCH",
            f"recomputed subtotal {subtotal} != claimed {claimed_subtotal_paise}",
            {"computed_paise": subtotal, "claimed_paise": claimed_subtotal_paise},
        )

    expected_total = final_total_paise(subtotal, claimed_discount_paise)
    if claimed_total_paise is not None and expected_total != claimed_total_paise:
        raise ValidationError(
            "TOTAL_MISMATCH",
            f"recomputed total {expected_total} != claimed {claimed_total_paise}",
            {"computed_paise": expected_total, "claimed_paise": claimed_total_paise},
        )


def assert_sells_above_cost(unit_price_paise: int, cost_paise: int, *, sku: str = "") -> None:
    """Refuse a line priced below what the item cost.

    Selling under cost is occasionally deliberate, but never by accident, and
    an agent negotiating a discount is exactly the path where it happens by
    accident. The policy engine may override; nothing may do so silently.
    """
    if unit_price_paise < cost_paise:
        raise ValidationError(
            "PRICE_BELOW_COST",
            f"price {unit_price_paise} is below cost {cost_paise}"
            + (f" for {sku}" if sku else ""),
            {"sku": sku, "unit_price_paise": unit_price_paise, "cost_paise": cost_paise},
        )


def assert_in_stock(*, sku: str, requested: int, available: int) -> None:
    """Refuse to sell more than exists."""
    if requested > available:
        raise ValidationError(
            "INSUFFICIENT_STOCK",
            f"requested {requested} of {sku} but only {available} available",
            {"sku": sku, "requested": requested, "available": available},
        )


__all__ = [
    "MAX_CART_LINES",
    "MAX_LINE_QUANTITY",
    "MAX_ORDER_TOTAL_PAISE",
    "MAX_UNIT_PRICE_PAISE",
    "assert_in_stock",
    "assert_sells_above_cost",
    "assert_totals_reconcile",
    "cart_subtotal_paise",
    "final_total_paise",
    "line_total_paise",
    "validate_discount_paise",
    "validate_price_paise",
    "validate_quantity",
]
