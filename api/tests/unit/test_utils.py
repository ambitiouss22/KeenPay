"""Unit tests for utils."""

from utils.money import compute_discount_amount_paise, format_inr, paise_to_rupees, rupees_to_paise
from utils.validators import is_valid_sku, sanitize_user_text


def test_paise_to_rupees():
    assert paise_to_rupees(449800) == "4,498.00"


def test_rupees_to_paise():
    assert rupees_to_paise(44.99) == 4499


def test_format_inr():
    assert format_inr(99900) == "₹999.00"


def test_discount_paise():
    assert compute_discount_amount_paise(100000, 10) == 10000


def test_sku_validator():
    assert is_valid_sku("HOODIE-NAVY-M")
    assert not is_valid_sku("bad sku!")


def test_sanitize_user_text():
    assert sanitize_user_text("  hello\x00world  ") == "helloworld"
