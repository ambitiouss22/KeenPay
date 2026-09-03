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

    # --- financial action limits (phase 5) ---------------------------------
    # Every limit here is a *hard* number rather than a percentage of
    # something, because a limit expressed relative to a value an attacker can
    # inflate is not a limit. Two thresholds per dimension, deliberately:
    # one that escalates to a human, one that refuses outright. A single
    # threshold forces a choice between blocking legitimate large orders and
    # waving through fraudulent ones.

    #: Refuse any single payment above this. ₹5,00,000.
    max_payment_paise: int = 50_000_000
    #: Above this a payment needs a human. ₹1,00,000.
    escalate_payment_above_paise: int = 10_000_000
    #: Refuse any single refund above this. ₹5,00,000.
    max_refund_paise: int = 50_000_000
    #: Above this a refund needs a human. ₹25,000. Lower than the payment
    #: threshold on purpose: money leaving is harder to claw back than money
    #: arriving, so it earns scrutiny sooner.
    escalate_refund_above_paise: int = 2_500_000
    #: Refuse a payout above this. ₹10,00,000.
    max_payout_paise: int = 100_000_000
    #: Refuse campaign spend above this. ₹2,00,000.
    max_campaign_spend_paise: int = 20_000_000
    #: Total money this merchant may move in a day, all kinds. ₹20,00,000.
    daily_total_cap_paise: int = 200_000_000
    #: Actions of one kind per hour before escalating, and before refusing.
    escalate_actions_per_hour_above: int = 20
    max_actions_per_hour: int = 60
    #: A refund is only eligible within this many days of capture.
    refund_window_days: int = 180
    #: Approvals needed when risk comes back high. Two is a quorum: it takes
    #: two compromised accounts rather than one to move flagged money.
    quorum_approvals: int = 2
    #: A pending authorization dies after this long. Short, because a stale
    #: approval is an approval granted against facts that no longer hold.
    authorization_ttl_seconds: int = 900
    #: Countries a buyer may transact from. Empty means "no restriction".
    allowed_countries: list[str] = Field(default_factory=lambda: ["IN"])
    #: Countries refused outright regardless of the allow list.
    blocked_countries: list[str] = Field(default_factory=list)


@lru_cache
def load_merchant_policy(merchant_id: str = "merchant_keen") -> MerchantPolicy:
    settings = get_settings()
    path = Path(settings.merchant_policy_json)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("merchant_id", merchant_id)
        return MerchantPolicy(**data)
    return MerchantPolicy(merchant_id=merchant_id)
