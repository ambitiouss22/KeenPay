"""Risk scoring: the four signals and how they combine.

The aggregation is the part worth testing hardest. A weighted average would
pass most of the tests below and fail the one that matters - a single severe
signal must not be diluted into "low" by three quiet ones, because a confirmed
fraud indicator is not made safe by the transaction being unremarkable in every
other respect.
"""

from __future__ import annotations

import pytest

from config.policy import MerchantPolicy
from modules.risk.service import LOW_MAX, MEDIUM_MAX, RiskBand, RiskService
from policy.models import ActionKind, FinancialAction

POLICY = MerchantPolicy()
RISK = RiskService()


def action(**overrides) -> FinancialAction:
    base = {
        "kind": ActionKind.PAYMENT,
        "merchant_id": "merchant_keen",
        "amount_paise": 250_000,
        "subject_id": "ord_abc123",
        "actor_id": "user_shopper",
        "actor_role": "shopper",
    }
    return FinancialAction(**{**base, **overrides})


def band(**overrides) -> RiskBand:
    return RISK.assess(action(**overrides), policy=POLICY).band


def score(**overrides) -> float:
    return RISK.assess(action(**overrides), policy=POLICY).score


# --- the quiet case ---------------------------------------------------------


def test_an_ordinary_payment_scores_zero():
    """An established buyer paying a normal amount from the usual country must
    score nothing at all. A scorer that returns "a little bit" for every
    transaction adds a constant to every score and moves the band edges without
    anyone deciding to."""
    assert score() == 0.0
    assert band() is RiskBand.LOW


def test_scoring_is_deterministic():
    a = action(amount_paise=15_000_000, buyer_age_days=2)
    assert RISK.assess(a, policy=POLICY).score == RISK.assess(a, policy=POLICY).score


def test_an_assessment_is_immutable():
    """It is evidence about a moment, not a working value."""
    assessment = RISK.assess(action(), policy=POLICY)
    with pytest.raises((AttributeError, TypeError)):
        assessment.score = 0.99


# --- amount -----------------------------------------------------------------


def test_a_small_amount_contributes_nothing():
    assert RISK.assess(action(amount_paise=1_000), policy=POLICY).components["amount"] == 0.0


def test_the_score_rises_with_the_amount():
    ladder = [
        score(amount_paise=1_000),
        score(amount_paise=9_000_000),
        score(amount_paise=15_000_000),
        score(amount_paise=35_000_000),
    ]
    assert ladder == sorted(ladder), ladder
    assert len(set(ladder)) > 1, "the amount signal is not moving the score"


def test_just_over_the_threshold_is_medium_not_high():
    """A cliff where one rupee more demands a second approver teaches people to
    split payments to stay under it."""
    assert band(amount_paise=POLICY.escalate_payment_above_paise + 1) is RiskBand.MEDIUM


def test_far_over_the_threshold_is_high():
    assert band(amount_paise=POLICY.escalate_payment_above_paise * 3) is RiskBand.HIGH


def test_each_kind_is_scored_against_its_own_reference():
    """A payout that empties an account must not read as ordinary just because
    a payment of the same size would be."""
    amount = 30_000_000
    payment = RISK.assess(action(amount_paise=amount), policy=POLICY)
    payout = RISK.assess(
        action(kind=ActionKind.PAYOUT, actor_role="admin", amount_paise=amount), policy=POLICY
    )
    assert payment.components["amount"] != payout.components["amount"]


# --- geography --------------------------------------------------------------


def test_matching_countries_score_nothing():
    assert RISK.assess(action(buyer_country="IN", ip_country="IN"), policy=POLICY).components[
        "geography"
    ] == 0.0


def test_a_country_mismatch_is_a_signal():
    """Often a traveller or a VPN, occasionally a stolen card used from
    somewhere the buyer would rather not name. Worth one pair of eyes."""
    assert band(ip_country="RU") is RiskBand.MEDIUM


def test_a_blocked_country_maxes_the_signal():
    policy = MerchantPolicy(blocked_countries=["KP"])
    assessment = RISK.assess(action(buyer_country="KP", ip_country="KP"), policy=policy)
    assert assessment.components["geography"] == 1.0
    assert assessment.band is RiskBand.HIGH


def test_a_blocked_ip_counts_even_when_the_buyer_claims_otherwise():
    policy = MerchantPolicy(blocked_countries=["KP"])
    assert RISK.assess(action(ip_country="KP"), policy=policy).band is RiskBand.HIGH


def test_geography_is_case_insensitive():
    assert RISK.assess(action(buyer_country="in", ip_country="IN"), policy=POLICY).components[
        "geography"
    ] == 0.0


# --- new buyer --------------------------------------------------------------


def test_a_brand_new_account_is_a_signal():
    assert RISK.assess(
        action(buyer_age_days=0, buyer_prior_orders=0), policy=POLICY
    ).components["new_buyer"] == pytest.approx(0.8)


def test_a_new_buyer_alone_never_reaches_high():
    """Fraud has no history and neither does a first-time customer; the two are
    indistinguishable here. A system that refused every first purchase would
    have no second ones."""
    assert band(buyer_age_days=0, buyer_prior_orders=0) is RiskBand.MEDIUM


def test_an_established_buyer_scores_nothing():
    assert (
        RISK.assess(action(buyer_age_days=900, buyer_prior_orders=40), policy=POLICY).components[
            "new_buyer"
        ]
        == 0.0
    )


def test_a_young_account_with_history_scores_lower_than_one_without():
    with_history = RISK.assess(
        action(buyer_age_days=3, buyer_prior_orders=5), policy=POLICY
    ).components["new_buyer"]
    without = RISK.assess(
        action(buyer_age_days=3, buyer_prior_orders=0), policy=POLICY
    ).components["new_buyer"]
    assert with_history < without


# --- velocity ---------------------------------------------------------------


def test_a_quiet_hour_scores_nothing():
    assert RISK.assess(action(actions_last_hour=2), policy=POLICY).components["velocity"] == 0.0


def test_velocity_rises_with_the_count():
    ladder = [
        score(actions_last_hour=1),
        score(actions_last_hour=12),
        score(actions_last_hour=25),
        score(actions_last_hour=90),
    ]
    assert ladder == sorted(ladder), ladder


def test_sustained_velocity_reaches_high():
    assert band(actions_last_hour=POLICY.escalate_actions_per_hour_above + 5) is RiskBand.HIGH


def test_velocity_uses_the_same_thresholds_as_policy():
    """Risk and policy must not drift into disagreeing about what "fast" means."""
    just_under = RISK.assess(
        action(actions_last_hour=POLICY.escalate_actions_per_hour_above - 1), policy=POLICY
    ).components["velocity"]
    at_it = RISK.assess(
        action(actions_last_hour=POLICY.escalate_actions_per_hour_above), policy=POLICY
    ).components["velocity"]
    assert at_it > just_under


# --- aggregation ------------------------------------------------------------


def test_one_severe_signal_is_not_diluted_by_three_quiet_ones():
    """The reason this is not a weighted average.

    A maxed-out geography flag averaged with three zeroes scores 0.25 and reads
    as "low" - precisely backwards.
    """
    policy = MerchantPolicy(blocked_countries=["KP"])
    assessment = RISK.assess(
        action(
            amount_paise=1_000,  # quiet
            buyer_age_days=900,  # quiet
            buyer_prior_orders=50,  # quiet
            buyer_country="KP",  # severe
            ip_country="KP",
        ),
        policy=policy,
    )
    assert assessment.band is RiskBand.HIGH


def test_corroborating_signals_escalate_the_score():
    alone = score(amount_paise=15_000_000)
    corroborated = score(amount_paise=15_000_000, buyer_age_days=0, buyer_prior_orders=0)
    assert corroborated > alone
    assert RISK.band_for(corroborated) is RiskBand.HIGH


def test_weak_signals_cannot_accumulate_into_certainty():
    """Saturating aggregation: extra signals always raise the score but can
    never reach 1.0 on their own. Otherwise enough weak evidence eventually
    convicts everybody."""
    assert score(buyer_age_days=20, buyer_prior_orders=1, actions_last_hour=12) < 1.0


def test_the_score_stays_within_bounds():
    policy = MerchantPolicy(blocked_countries=["KP"])
    worst = RISK.assess(
        action(
            amount_paise=POLICY.max_payment_paise,
            buyer_age_days=0,
            buyer_prior_orders=0,
            buyer_country="KP",
            ip_country="KP",
            actions_last_hour=500,
        ),
        policy=policy,
    )
    assert 0.0 <= worst.score <= 1.0
    assert worst.band is RiskBand.HIGH


# --- bands and reporting ----------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, RiskBand.LOW),
        (LOW_MAX - 0.001, RiskBand.LOW),
        (LOW_MAX, RiskBand.MEDIUM),
        (MEDIUM_MAX - 0.001, RiskBand.MEDIUM),
        (MEDIUM_MAX, RiskBand.HIGH),
        (1.0, RiskBand.HIGH),
    ],
)
def test_band_edges_are_where_they_are_documented(value, expected):
    assert RiskService.band_for(value) is expected


def test_signals_are_only_listed_when_they_fired():
    assert RISK.assess(action(), policy=POLICY).signals == []
    noisy = RISK.assess(action(buyer_age_days=0, buyer_prior_orders=0), policy=POLICY)
    assert any("new_buyer" in s for s in noisy.signals)


def test_an_assessment_serialises_for_storage():
    payload = RISK.assess(action(ip_country="RU"), policy=POLICY).to_dict()
    assert set(payload) == {"score", "band", "signals", "components", "metadata"}
    assert payload["band"] in {"low", "medium", "high"}
    assert set(payload["components"]) == {"amount", "geography", "new_buyer", "velocity"}
