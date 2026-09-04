"""Snapshot hashing: what an authorization is actually bound to."""

import pytest

from modules.payments.snapshot import (
    _exact_int,
    assert_snapshot_unchanged,
    order_snapshot,
    snapshot_hash,
)


def _order(lines, **overrides):
    total = sum(line["list_price_paise"] * line["quantity"] for line in lines)
    order = {
        "line_items": lines,
        "subtotal_paise": total,
        "discount_amount_paise": 0,
        "final_amount_paise": total,
        "currency": "INR",
    }
    order.update(overrides)
    return order


def _line(sku, price=100, cost=50, quantity=1):
    return {
        "sku": sku,
        "name": sku,
        "quantity": quantity,
        "list_price_paise": price,
        "cost_paise": cost,
    }


def test_float_money_is_refused():
    """249.9 must not quietly become 249."""
    with pytest.raises(ValueError):
        _exact_int(249.9)


def test_bool_money_is_refused():
    """True == 1 in Python; that is not a price."""
    with pytest.raises(ValueError):
        _exact_int(True)


def test_int_money_is_accepted():
    assert _exact_int(250) == 250


def test_a_line_missing_a_field_is_refused():
    with pytest.raises(ValueError):
        order_snapshot({"line_items": [{"sku": "X"}]})


def test_churn_does_not_change_the_hash():
    """A save that only touches timestamps must not invalidate an approval."""
    first = _order([_line("X")], created_at="2026-01-01T00:00:00Z", status="pending")
    second = _order([_line("X")], created_at="2026-01-02T00:00:00Z", status="paid")
    assert snapshot_hash(first) == snapshot_hash(second)


def test_swapping_goods_at_the_same_total_changes_the_hash():
    """The attack: same money, different items."""
    before = _order([_line("A", 100), _line("B", 50)])
    after = _order([_line("C", 100), _line("D", 50)])
    assert snapshot_hash(before) != snapshot_hash(after)


def test_line_order_does_not_matter():
    forwards = _order([_line("A", 100), _line("B", 50)])
    backwards = _order([_line("B", 50), _line("A", 100)])
    assert snapshot_hash(forwards) == snapshot_hash(backwards)


def test_changing_the_price_changes_the_hash():
    assert snapshot_hash(_order([_line("A", 100)])) != snapshot_hash(_order([_line("A", 101)]))


def test_mutation_after_authorization_is_detected():
    original = _order([_line("A")])
    mutated = _order([_line("B")])
    with pytest.raises(ValueError):
        assert_snapshot_unchanged(snapshot_hash(original), mutated)


def test_an_unchanged_cart_passes():
    order = _order([_line("A")])
    assert_snapshot_unchanged(snapshot_hash(order), order)
