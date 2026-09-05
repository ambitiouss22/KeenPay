"""Budget arithmetic.

Pure unit tests over ``modules.campaigns.budget``. Everything the hard cap is
built on is decided here, so it is worth pinning precisely: what counts as a
valid amount, what ``remaining`` means, and what a percentage ceiling floors to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.exceptions import ConflictError, ValidationError
from modules.campaigns.budget import (
    MAX_CAMPAIGN_BUDGET_PAISE,
    BudgetExceededError,
    BudgetState,
    CampaignInactiveError,
    assert_campaign_spendable,
    assert_releasable,
    assert_reservable,
    max_discount_paise,
    normalise_discount_pct,
    validate_budget_paise,
    validate_reservation_paise,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _campaign(**overrides):
    row = {
        "id": "camp_1",
        "active": True,
        "budget_paise": 100_000,
        "reserved_paise": 0,
        "spent_paise": 0,
        "starts_at": None,
        "ends_at": None,
    }
    row.update(overrides)
    return row


# --- the counters ----------------------------------------------------------


def test_remaining_is_budget_less_everything_committed():
    state = BudgetState(budget_paise=100_000, reserved_paise=25_000, spent_paise=15_000)
    assert state.committed_paise == 40_000
    assert state.remaining_paise == 60_000
    assert state.exhausted is False


def test_a_fully_committed_budget_is_exhausted():
    state = BudgetState(budget_paise=1_000, reserved_paise=600, spent_paise=400)
    assert state.remaining_paise == 0
    assert state.exhausted is True
    assert state.fits(1) is False


def test_reserved_and_spent_are_tracked_apart():
    """Settling does not change what is left; it changes what is releasable.

    Collapsing the two counters is the shape that either leaks budget on every
    abandoned checkout or lets a settled discount be handed out again.
    """
    before = BudgetState(budget_paise=10_000, reserved_paise=4_000, spent_paise=0)
    after = BudgetState(budget_paise=10_000, reserved_paise=0, spent_paise=4_000)
    assert before.remaining_paise == after.remaining_paise == 6_000


def test_from_row_tolerates_absent_counters():
    state = BudgetState.from_row({"budget_paise": 500})
    assert (state.reserved_paise, state.spent_paise, state.remaining_paise) == (0, 0, 500)


# --- what is a valid amount ------------------------------------------------


@pytest.mark.parametrize("value", [0, -1, -100_000])
def test_a_reservation_must_be_positive(value):
    with pytest.raises(ValidationError) as exc:
        validate_reservation_paise(value)
    assert exc.value.code == "INVALID_RESERVATION"


@pytest.mark.parametrize("value", [10.5, "100", None, Decimal("10")])
def test_a_reservation_must_be_a_real_integer(value):
    """A float is refused rather than rounded: rounding is the silent corruption."""
    with pytest.raises(ValidationError) as exc:
        validate_reservation_paise(value)
    assert exc.value.code == "INVALID_AMOUNT"


def test_a_boolean_is_not_a_quantity_of_one():
    """``True == 1`` in Python, so bool has to be refused explicitly."""
    with pytest.raises(ValidationError) as exc:
        validate_reservation_paise(True)
    assert exc.value.code == "INVALID_AMOUNT"


@pytest.mark.parametrize("value", [0, -5])
def test_a_budget_must_be_positive(value):
    with pytest.raises(ValidationError) as exc:
        validate_budget_paise(value)
    assert exc.value.code == "INVALID_BUDGET"


def test_an_absurd_budget_is_refused_not_clamped():
    with pytest.raises(ValidationError) as exc:
        validate_budget_paise(MAX_CAMPAIGN_BUDGET_PAISE + 1)
    assert exc.value.code == "BUDGET_TOO_LARGE"


def test_the_largest_permitted_budget_is_accepted():
    assert validate_budget_paise(MAX_CAMPAIGN_BUDGET_PAISE) == MAX_CAMPAIGN_BUDGET_PAISE


# --- reserving and releasing ------------------------------------------------


def test_a_reservation_that_fits_is_returned_unchanged():
    state = BudgetState(budget_paise=1_000, reserved_paise=0, spent_paise=0)
    assert assert_reservable(state, 1_000) == 1_000


def test_a_reservation_one_paisa_over_is_refused():
    state = BudgetState(budget_paise=1_000, reserved_paise=999, spent_paise=0)
    with pytest.raises(BudgetExceededError) as exc:
        assert_reservable(state, 2, campaign_id="camp_1")
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert exc.value.status_code == 409
    # The caller is told what it may ask for instead, not merely that it failed.
    assert exc.value.details["remaining_paise"] == 1
    assert exc.value.details["requested_paise"] == 2


def test_spent_money_counts_against_the_cap_too():
    state = BudgetState(budget_paise=1_000, reserved_paise=0, spent_paise=1_000)
    with pytest.raises(BudgetExceededError):
        assert_reservable(state, 1)


def test_releasing_more_than_is_reserved_is_refused():
    """Otherwise the excess reappears as headroom nobody funded."""
    state = BudgetState(budget_paise=1_000, reserved_paise=300, spent_paise=0)
    with pytest.raises(ConflictError) as exc:
        assert_releasable(state, 301, campaign_id="camp_1")
    assert exc.value.code == "RELEASE_EXCEEDS_RESERVED"


def test_releasing_exactly_what_is_reserved_is_allowed():
    state = BudgetState(budget_paise=1_000, reserved_paise=300, spent_paise=0)
    assert assert_releasable(state, 300) == 300


# --- the campaign window ----------------------------------------------------


def test_an_inactive_campaign_cannot_be_spent():
    with pytest.raises(CampaignInactiveError) as exc:
        assert_campaign_spendable(_campaign(active=False), now=NOW)
    assert exc.value.code == "CAMPAIGN_INACTIVE"


def test_a_campaign_that_has_not_started_cannot_be_spent():
    row = _campaign(starts_at=NOW + timedelta(days=1))
    with pytest.raises(CampaignInactiveError) as exc:
        assert_campaign_spendable(row, now=NOW)
    assert exc.value.code == "CAMPAIGN_NOT_STARTED"


def test_a_finished_campaign_cannot_be_spent():
    row = _campaign(ends_at=NOW - timedelta(seconds=1))
    with pytest.raises(CampaignInactiveError) as exc:
        assert_campaign_spendable(row, now=NOW)
    assert exc.value.code == "CAMPAIGN_ENDED"


def test_a_campaign_inside_its_window_is_spendable():
    row = _campaign(starts_at=NOW - timedelta(days=1), ends_at=NOW + timedelta(days=1))
    assert_campaign_spendable(row, now=NOW)


def test_a_naive_stored_timestamp_is_read_as_utc():
    """The in-memory store and Postgres do not always agree on tzinfo.

    Comparing a naive datetime with an aware one raises, which would turn a
    storage detail into a 500 on the reservation path.
    """
    row = _campaign(ends_at=datetime(2026, 1, 2, 12, 0))  # deliberately naive
    assert_campaign_spendable(row, now=NOW)


# --- percentage ceilings ----------------------------------------------------


def test_a_percentage_cap_is_floored_never_rounded_up():
    """Rounding up would let a campaign fund a paisa more than its own cap."""
    state = BudgetState(budget_paise=1_000_000, reserved_paise=0, spent_paise=0)
    # 12.5% of 999 paise is 124.875.
    assert max_discount_paise(state, subtotal_paise=999, max_discount_pct="12.5") == 124


def test_the_budget_caps_the_percentage():
    state = BudgetState(budget_paise=500, reserved_paise=0, spent_paise=0)
    assert max_discount_paise(state, subtotal_paise=10_000, max_discount_pct=50) == 500


def test_no_percentage_cap_leaves_the_budget_as_the_only_limit():
    state = BudgetState(budget_paise=5_000, reserved_paise=0, spent_paise=0)
    assert max_discount_paise(state, subtotal_paise=1_000, max_discount_pct=None) == 1_000


def test_a_discount_never_exceeds_the_order_it_discounts():
    """A discount larger than the subtotal is a refund wearing a discount's name."""
    state = BudgetState(budget_paise=1_000_000, reserved_paise=0, spent_paise=0)
    assert max_discount_paise(state, subtotal_paise=100, max_discount_pct=100) == 100


def test_an_exhausted_campaign_funds_nothing():
    state = BudgetState(budget_paise=1_000, reserved_paise=1_000, spent_paise=0)
    assert max_discount_paise(state, subtotal_paise=50_000, max_discount_pct=90) == 0


@pytest.mark.parametrize("value", [-1, 101, "abc", True])
def test_an_out_of_range_percentage_is_refused(value):
    with pytest.raises(ValidationError) as exc:
        normalise_discount_pct(value)
    assert exc.value.code == "INVALID_DISCOUNT_PCT"


def test_a_percentage_is_carried_exactly():
    """Via ``str``, so 12.5 is 12.5 and not the binary approximation."""
    assert normalise_discount_pct(12.5) == Decimal("12.5")
    assert normalise_discount_pct(None) is None
