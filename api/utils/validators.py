"""Input validators for catalog and session payloads."""

import re

SKU_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_]{1,62}$")
MERCHANT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def is_valid_sku(sku: str) -> bool:
    return bool(SKU_PATTERN.match(sku))


def is_valid_merchant_id(merchant_id: str) -> bool:
    return bool(MERCHANT_ID_PATTERN.match(merchant_id))


def sanitize_user_text(text: str, *, max_length: int = 4000) -> str:
    """Strip control chars; cap length before LLM/policy pipeline."""
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return cleaned[:max_length].strip()
