"""Merchant policy configuration."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from config.settings import get_settings


class MerchantPolicy(BaseModel):
    policy_version: str = "2026.08.1"
    merchant_id: str = "merchant_keen"
    currency: Literal["INR"] = "INR"
    max_discount_pct: float = 15.0
    max_discount_pct_per_sku: dict[str, float] = Field(default_factory=dict)
    max_absolute_discount_paise: int = 50_000
    min_margin_pct: float = 20.0
    cost_basis_field: Literal["cost_paise", "wholesale_paise"] = "cost_paise"
    max_qty_per_line: int = 10
    max_qty_per_order: int = 20
    allow_backorder: bool = False
    max_negotiation_rounds: int = 5
    max_payment_links_per_session_per_hour: int = 3
    block_on_anomaly_score_gte: float = 0.85


@lru_cache
def load_merchant_policy(merchant_id: str = "merchant_keen") -> MerchantPolicy:
    settings = get_settings()
    path = Path(settings.merchant_policy_json)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("merchant_id", merchant_id)
        return MerchantPolicy(**data)
    return MerchantPolicy(merchant_id=merchant_id)
