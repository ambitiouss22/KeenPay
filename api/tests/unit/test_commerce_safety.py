"""Money and quantity invariants.

Pure-function tests, so they are fast and exhaustive. This is the layer where a
rounding error becomes a wrong charge, so the cases below are deliberately
adversarial rather than representative.
"""

from __future__ import annotations

import pytest

from core.exceptions import ValidationError
from modules.commerce.safety import (
    MAX_CART_LINES,
    MAX_LINE_QUANTITY,
    MAX_ORDER_TOTAL_PAISE,
    MAX_UNIT_PRICE_PAISE,
    assert_in_stock,
    assert_sells_above_cost,
    assert_totals_reconcile,
    cart_subtotal_paise,
    final_total_paise,
    line_total_paise,
    validate_discount_paise,
    validate_price_paise,
    validate_quantity,
)


def line(price: int, qty: int) -> dict:
    return {"unit_price_paise": price, "quantity": qty}


# --- quantities -------------------------------------------------------------


def test_accepts_a_normal_quantity():
    assert validate_quantity(3) == 3


@pytest.mark.parametrize("bad", [0, -1, -1000])
def test_rejects_non_positive_quantities(bad):
    with pytest.raises(ValidationError):
        validate_quantity(bad)


def test_rejects_absurd_quantity():
    with pytest.raises(ValidationError):
        validate_quantity(MAX_LINE_QUANTITY + 1)


@pytest.mark.parametrize("bad", [1.5, "3", None, [1], 2.0])
def test_rejects_non_integer_quantities(bad):
    with pytest.raises(ValidationError):
        validate_quantity(bad)


def test_rejects_booleans_as_quantity():
    """bool subclasses int, so True would otherwise pass as a quantity of 1."""
    with pytest.raises(ValidationError):
        validate_quantity(True)


# --- prices -----------------------------------------------------------------


def test_zero_price_is_allowed():
    assert validate_price_paise(0) == 0


def test_rejects_negative_price():
    with pytest.raises(ValidationError):
        validate_price_paise(-1)


def test_rejects_price_above_ceiling():
    with pytest.raises(ValidationError):
        validate_price_paise(MAX_UNIT_PRICE_PAISE + 1)


@pytest.mark.parametrize("bad", [249.9, 250.0, "250", None, True])
def test_rejects_non_integer_price(bad):
    """A float price is the bug this whole module exists to prevent."""
    with pytest.raises(ValidationError):
        validate_price_paise(bad)


# --- line and cart arithmetic ----------------------------------------------


def test_line_total_is_exact():
    assert line_total_paise(24999, 3) == 74997


def test_line_total_rejects_a_float_price():
    with pytest.raises(ValidationError):
        line_total_paise(249.99, 2)


def test_cart_subtotal_sums_lines():
    assert cart_subtotal_paise([line(10000, 2), line(2500, 3)]) == 27500


def test_empty_cart_subtotal_is_zero():
    assert cart_subtotal_paise([]) == 0


def test_rejects_too_many_lines():
    with pytest.raises(ValidationError):
        cart_subtotal_paise([line(1, 1)] * (MAX_CART_LINES + 1))


def test_rejects_a_cart_total_beyond_the_ceiling():
    with pytest.raises(ValidationError):
        cart_subtotal_paise([line(MAX_UNIT_PRICE_PAISE, MAX_LINE_QUANTITY)] * 2)


def test_no_floating_point_drift_over_many_lines():
    """The reason money is integer paise: 0.1 + 0.2 != 0.3 in binary floats."""
    items = [line(10, 1)] * 100
    assert cart_subtotal_paise(items) == 1000


# --- discounts --------------------------------------------------------------


def test_discount_within_subtotal_is_fine():
    assert validate_discount_paise(500, 1000) == 500


def test_discount_equal_to_subtotal_is_allowed():
    assert final_total_paise(1000, 1000) == 0


def test_rejects_negative_discount():
    with pytest.raises(ValidationError):
        validate_discount_paise(-1, 1000)


def test_rejects_discount_larger_than_subtotal():
    """A total below zero would be a refund, not a sale."""
    with pytest.raises(ValidationError):
        validate_discount_paise(1001, 1000)


def test_final_total_is_never_negative():
    assert final_total_paise(1000, 999) == 1


# --- reconciliation ---------------------------------------------------------


def test_reconcile_accepts_honest_figures():
    items = [line(10000, 2)]
    assert_totals_reconcile(
        items, claimed_subtotal_paise=20000, claimed_discount_paise=0, claimed_total_paise=20000
    )


def test_reconcile_rejects_an_understated_subtotal():
    """The attack: a client claims a smaller subtotal than its lines add to."""
    with pytest.raises(ValidationError):
        assert_totals_reconcile([line(10000, 2)], claimed_subtotal_paise=1)


def test_reconcile_rejects_a_wrong_total():
    with pytest.raises(ValidationError):
        assert_totals_reconcile(
            [line(10000, 2)],
            claimed_subtotal_paise=20000,
            claimed_discount_paise=5000,
            claimed_total_paise=1,
        )


def test_reconcile_catches_a_discount_that_exceeds_the_cart():
    with pytest.raises(ValidationError):
        assert_totals_reconcile(
            [line(100, 1)], claimed_subtotal_paise=100, claimed_discount_paise=999
        )


# --- cost and stock ---------------------------------------------------------


def test_selling_above_cost_is_fine():
    assert_sells_above_cost(1000, 500, sku="X")


def test_selling_at_cost_is_allowed():
    assert_sells_above_cost(500, 500, sku="X")


def test_selling_below_cost_is_refused():
    with pytest.raises(ValidationError):
        assert_sells_above_cost(499, 500, sku="X")


def test_stock_within_availability_is_fine():
    assert_in_stock(sku="X", requested=3, available=3)


def test_stock_beyond_availability_is_refused():
    with pytest.raises(ValidationError):
        assert_in_stock(sku="X", requested=4, available=3)


def test_ceilings_are_ordered_sensibly():
    """A single max-price unit must not already exceed the order ceiling."""
    assert MAX_UNIT_PRICE_PAISE <= MAX_ORDER_TOTAL_PAISE
