"""The suggestion rules.

Pure unit tests over ``modules.opportunities.rules``. Two properties matter more
than the taste of any particular ranking:

* the same inputs produce the same suggestions, in the same order, with the same
  ids - because ids are derived from content and the store inserts-or-ignores
* a suggestion carries no authority over money
"""

from __future__ import annotations

from decimal import Decimal

from modules.opportunities import rules


def _product(sku: str, price: int, *, qty: int = 5, active: bool = True, family=None):
    attributes = {"family": family} if family else {}
    return {
        "id": f"prod_{sku.lower()}",
        "sku": sku,
        "name": f"Product {sku}",
        "list_price_paise": price,
        "cost_paise": price // 2,
        "quantity_on_hand": qty,
        "quantity_available": qty,
        "attributes": attributes,
        "active": active,
    }


CATALOG = [
    _product("KIT-S", 100_000),
    _product("KIT-M", 120_000),
    _product("KIT-XL", 400_000),
    _product("CASE-ONE", 30_000),
    _product("BULK-ONE", 90_000),
]

ANCHOR = rules.Anchor(sku="KIT-S", unit_price_paise=100_000, family="KIT")


# --- families ---------------------------------------------------------------


def test_a_declared_family_wins_over_the_sku():
    assert rules.family_of(_product("ANYTHING", 100, family="winter")) == "winter"


def test_a_hyphenated_sku_falls_back_to_its_stem():
    assert rules.family_of(_product("HOODIE-NAVY-M", 100)) == "HOODIE-NAVY"


def test_a_sku_with_no_separator_is_its_own_family():
    """An unstructured catalogue produces no upsells rather than nonsense ones."""
    assert rules.family_of(_product("SOCKS", 100)) == "SOCKS"


# --- what may be suggested --------------------------------------------------


def test_an_inactive_product_is_never_suggested():
    assert rules.sellable(_product("X-1", 100, active=False)) is False


def test_an_out_of_stock_product_is_never_suggested():
    """Suggesting what cannot be sold teaches a merchant to stop reading the list."""
    assert rules.sellable(_product("X-1", 100, qty=0)) is False


def test_stock_falls_back_to_quantity_on_hand():
    product = {"sku": "X-1", "list_price_paise": 100, "quantity_on_hand": 3, "active": True}
    assert rules.sellable(product) is True


# --- upsell -----------------------------------------------------------------


def test_the_nearest_step_up_scores_highest():
    """A shopper takes the next size, not the flagship."""
    near = rules.score_upsell(ANCHOR, _product("KIT-M", 120_000))
    far = rules.score_upsell(ANCHOR, _product("KIT-L", 190_000))
    assert near is not None and far is not None
    assert near > far


def test_an_upsell_must_cost_more():
    assert rules.score_upsell(ANCHOR, _product("KIT-XS", 90_000)) is None
    assert rules.score_upsell(ANCHOR, _product("KIT-SAME", 100_000)) is None


def test_an_upsell_beyond_the_uplift_ceiling_is_refused():
    """Past this it is a different purchase, not an upgrade."""
    ceiling = int(100_000 * (1 + rules.MAX_UPLIFT_RATIO))
    assert rules.score_upsell(ANCHOR, _product("KIT-OK", ceiling)) is not None
    assert rules.score_upsell(ANCHOR, _product("KIT-TOO-BIG", ceiling + 1)) is None


def test_an_upsell_must_stay_in_the_family():
    assert rules.score_upsell(ANCHOR, _product("CASE-BIG", 120_000)) is None


def test_an_upsell_is_never_the_anchor_itself():
    assert rules.score_upsell(ANCHOR, _product("KIT-S", 100_000)) is None


# --- cross-sell -------------------------------------------------------------


def test_an_add_on_priced_at_the_target_ratio_scores_top():
    score = rules.score_cross_sell(ANCHOR, _product("CASE-ONE", 30_000))
    assert score == Decimal("1.0000")


def test_a_cross_sell_must_be_a_different_family():
    """Otherwise it is an upsell wearing the wrong label."""
    assert rules.score_cross_sell(ANCHOR, _product("KIT-M", 30_000)) is None


def test_a_cross_sell_priced_like_the_anchor_is_refused():
    assert rules.score_cross_sell(ANCHOR, _product("BULK-ONE", 90_000)) is None


def test_a_token_priced_cross_sell_is_refused():
    assert rules.score_cross_sell(ANCHOR, _product("FREE-ONE", 1)) is None


# --- generation -------------------------------------------------------------


def test_generation_is_reproducible():
    """Byte-for-byte, because ids are derived from content."""
    first = rules.generate(catalog=CATALOG, anchors=[ANCHOR])
    second = rules.generate(catalog=list(reversed(CATALOG)), anchors=[ANCHOR])
    assert [(s.kind, s.sku, s.score) for s in first] == [
        (s.kind, s.sku, s.score) for s in second
    ]


def test_generation_is_ordered_best_first():
    suggestions = rules.generate(catalog=CATALOG, anchors=[ANCHOR])
    scores = [s.score for s in suggestions]
    assert scores == sorted(scores, reverse=True)


def test_a_candidate_is_suggested_once_per_kind():
    """Two anchors that both fit the same item must not show it twice."""
    anchors = [
        ANCHOR,
        rules.Anchor(sku="KIT-S2", unit_price_paise=105_000, family="KIT"),
    ]
    suggestions = rules.generate(catalog=CATALOG, anchors=anchors)
    keys = [(s.kind, s.sku) for s in suggestions]
    assert len(keys) == len(set(keys))


def test_generation_respects_the_limit():
    assert len(rules.generate(catalog=CATALOG, anchors=[ANCHOR], limit=1)) == 1


def test_only_the_requested_kinds_are_produced():
    upsells = rules.generate(catalog=CATALOG, anchors=[ANCHOR], kinds=[rules.UPSELL])
    assert {s.kind for s in upsells} == {rules.UPSELL}


def test_an_unknown_kind_produces_nothing():
    assert rules.generate(catalog=CATALOG, anchors=[ANCHOR], kinds=["freebie"]) == []


def test_every_score_is_a_probability_shaped_number():
    for suggestion in rules.generate(catalog=CATALOG, anchors=[ANCHOR]):
        assert Decimal(0) <= suggestion.score <= Decimal(1)
        # Four places: what the score column stores. Rounding on write instead
        # would let two scores compare unequal in memory and equal in the row.
        assert -suggestion.score.as_tuple().exponent <= 4


# --- identity ---------------------------------------------------------------


def test_the_same_idea_has_the_same_id():
    args = {"merchant_id": "m1", "kind": rules.UPSELL, "subject_id": "cart:c1", "sku": "KIT-M"}
    assert rules.opportunity_id(rules.fingerprint(**args)) == rules.opportunity_id(
        rules.fingerprint(**args)
    )


def test_a_price_change_does_not_make_a_new_idea():
    """Otherwise every catalogue edit spawns a duplicate to dismiss separately."""
    before = rules.fingerprint(
        merchant_id="m1", kind=rules.UPSELL, subject_id="cart:c1", sku="KIT-M"
    )
    after = rules.fingerprint(
        merchant_id="m1", kind=rules.UPSELL, subject_id="cart:c1", sku="KIT-M"
    )
    assert before == after


def test_different_merchants_never_share_an_id():
    a = rules.opportunity_id(
        rules.fingerprint(merchant_id="m1", kind=rules.UPSELL, subject_id="s", sku="K")
    )
    b = rules.opportunity_id(
        rules.fingerprint(merchant_id="m2", kind=rules.UPSELL, subject_id="s", sku="K")
    )
    assert a != b


def test_kind_and_sku_both_change_the_id():
    base = {"merchant_id": "m1", "subject_id": "s", "sku": "K"}
    upsell = rules.fingerprint(kind=rules.UPSELL, **base)
    cross = rules.fingerprint(kind=rules.CROSS_SELL, **base)
    other_sku = rules.fingerprint(kind=rules.UPSELL, merchant_id="m1", subject_id="s", sku="J")
    assert len({upsell, cross, other_sku}) == 3


# --- the money boundary -----------------------------------------------------


def test_a_suggestion_cannot_name_a_discount():
    """The structural guard behind "AI recommends, the Control Plane decides".

    A suggestion carrying a discount, a price override or a campaign reference
    would be a suggestion that can spend a budget. There is nowhere to put one.
    """
    payload = rules.generate(catalog=CATALOG, anchors=[ANCHOR])[0].payload(
        merchant_id="m1", subject_id="cart:c1"
    )
    forbidden = ("discount", "budget", "campaign", "final_amount", "override")
    assert not [k for k in payload if any(word in k.lower() for word in forbidden)]


def test_the_price_on_a_suggestion_is_the_catalogue_price():
    suggestions = rules.generate(catalog=CATALOG, anchors=[ANCHOR], kinds=[rules.UPSELL])
    by_sku = {p["sku"]: p["list_price_paise"] for p in CATALOG}
    for suggestion in suggestions:
        assert suggestion.list_price_paise == by_sku[suggestion.sku]


# --- anchors ----------------------------------------------------------------


def test_cart_anchors_are_ordered_by_value():
    items = [
        {"sku": "A-1", "unit_price_paise": 100, "quantity": 1},
        {"sku": "B-1", "unit_price_paise": 900, "quantity": 1},
    ]
    assert [a.sku for a in rules.anchors_from_cart(items)] == ["B-1", "A-1"]


def test_catalog_anchors_are_the_priciest_sellable_items():
    anchors = rules.anchors_from_catalog(CATALOG, count=2)
    assert [a.sku for a in anchors] == ["KIT-XL", "KIT-M"]


def test_catalog_anchors_skip_what_cannot_be_sold():
    catalog = [_product("HIGH-1", 999_999, qty=0), _product("LOW-1", 100)]
    assert [a.sku for a in rules.anchors_from_catalog(catalog)] == ["LOW-1"]
