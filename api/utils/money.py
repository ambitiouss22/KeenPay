"""INR money helpers — integer paise only (never float for money)."""


def paise_to_rupees(paise: int) -> str:
    """Format paise as INR display string, e.g. 449800 -> '4,498.00'."""
    if paise < 0:
        raise ValueError("paise must be non-negative")
    rupees = paise // 100
    remainder = paise % 100
    return f"{rupees:,}.{remainder:02d}"


def rupees_to_paise(rupees: float) -> int:
    """Convert decimal rupees to integer paise (rounded)."""
    if rupees < 0:
        raise ValueError("rupees must be non-negative")
    return round(rupees * 100)


def format_inr(paise: int) -> str:
    """Human-readable INR label for chat/UI copy."""
    return f"₹{paise_to_rupees(paise)}"


def compute_discount_amount_paise(subtotal_paise: int, discount_pct: float) -> int:
    """Integer paise discount from percentage — no float drift on final amount."""
    if not 0 <= discount_pct <= 100:
        raise ValueError("discount_pct must be between 0 and 100")
    return round(subtotal_paise * discount_pct / 100)
