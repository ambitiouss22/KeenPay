"""Order snapshot hashing, so an authorization binds to *these* goods.

An approval that survives the cart changing underneath it is a signed blank
cheque: same total, different items. The snapshot covers the fields that decide
what is being bought and for how much, and nothing that merely churns
(timestamps, status), so a legitimate save does not invalidate an approval while
a swapped line does.
"""

import hashlib
import json
from typing import Any

LINE_FIELDS = ("cost_paise", "list_price_paise", "name", "quantity", "sku")


def _exact_int(val: Any) -> int:
    """Accept only an exact integer.

    ``bool`` is refused before ``int`` because ``True == 1``; a float is refused
    outright rather than truncated, since silently turning 249.9 into 249 is how
    money goes missing one paisa at a time.
    """
    if isinstance(val, bool):
        raise ValueError("Money must be an integer, not a bool")
    if isinstance(val, float):
        raise ValueError(f"Money must be an integer, not a float: {val}")
    if not isinstance(val, int):
        raise ValueError(f"Money must be an integer, got {type(val).__name__}")
    return val


def order_snapshot(order: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical, immutable view of an order."""
    lines = []
    for line in order.get("line_items", []):
        validated: dict[str, Any] = {}
        for key in LINE_FIELDS:
            if key not in line:
                raise ValueError(f"Line item is missing {key!r}")
            validated[key] = _exact_int(line[key]) if key.endswith("_paise") else line[key]
        lines.append(validated)

    lines.sort(key=lambda item: str(item["sku"]))

    return {
        "line_items": lines,
        "subtotal_paise": _exact_int(order.get("subtotal_paise", 0)),
        "discount_amount_paise": _exact_int(order.get("discount_amount_paise", 0)),
        "final_amount_paise": _exact_int(order.get("final_amount_paise", 0)),
        "currency": order.get("currency", "INR"),
    }


def snapshot_hash(order: dict[str, Any]) -> str:
    """Deterministic hash of the order snapshot."""
    canonical = json.dumps(order_snapshot(order), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def assert_snapshot_unchanged(original_hash: str, current_order: dict[str, Any]) -> None:
    """Raise if the cart moved after the authorization was granted."""
    current = snapshot_hash(current_order)
    if current != original_hash:
        raise ValueError(
            f"Cart mutated after authorization: expected {original_hash}, got {current}"
        )
