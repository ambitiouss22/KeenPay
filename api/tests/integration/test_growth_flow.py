"""The GROW loop, end to end over HTTP.

A merchant lists a catalogue, builds a cart, asks for growth suggestions, opens a
capped campaign, and reserves budget against it. Everything here goes through the
real routers, the real permission dependencies and the real stores.

The catalogue is shared module state that other test modules also write to, so
every product created here carries a run-unique sku and family. Assertions are
about *this* run's skus rather than about the whole list, which keeps the module
independent of what else has run first.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from repositories.campaigns import CampaignRepository, reset_campaigns
from repositories.carts import reset_carts
from repositories.opportunities import reset_opportunities

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"


@pytest.fixture(autouse=True)
def _clean_growth_stores():
    reset_campaigns()
    reset_opportunities()
    reset_carts()
    yield
    reset_campaigns()
    reset_opportunities()
    reset_carts()


#: One suffix for the whole module. The product store has no reset hook and is
#: shared with every other test module, so this module adds its five products
#: once rather than five per test - which keeps the catalogue small enough to
#: stay inside the scan limit however many modules run first.
UID = uuid.uuid4().hex[:8].upper()

SKUS = {
    "small": f"GK-{UID}-S",
    "medium": f"GK-{UID}-M",
    "huge": f"GK-{UID}-XL",
    "addon": f"AD{UID}-CASE",
    "bulky": f"BK{UID}-CRATE",
}


@pytest.fixture
def admin(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


async def _ensure_product(client, admin, *, sku, name, price, qty=20):
    """Create the product, or accept that an earlier test already did.

    Idempotent so the fixture can be function-scoped - which it has to be,
    because ``client`` is - without creating a fresh set of products per test.
    """
    response = await client.post(
        "/api/v1/products",
        headers=admin,
        json={
            "sku": sku,
            "name": name,
            "list_price_paise": price,
            "cost_paise": price // 4,
            "quantity_on_hand": qty,
        },
    )
    assert response.status_code in (201, 409), response.text


@pytest.fixture
async def catalogue(client: AsyncClient, admin):
    """A small, self-contained range: three sizes and two unrelated items.

    Prices are chosen so the rules have something definite to say about each
    one - a step up that qualifies, a step up that is too big, an add-on at the
    target ratio, and one priced too close to the anchor to be an add-on.
    """
    await _ensure_product(client, admin, sku=SKUS["small"], name="Kit (S)", price=100_000)
    await _ensure_product(client, admin, sku=SKUS["medium"], name="Kit (M)", price=120_000)
    await _ensure_product(client, admin, sku=SKUS["huge"], name="Kit (XL)", price=400_000)
    await _ensure_product(client, admin, sku=SKUS["addon"], name="Case", price=30_000)
    await _ensure_product(client, admin, sku=SKUS["bulky"], name="Crate", price=90_000)
    return SKUS


@pytest.fixture
async def cart(client: AsyncClient, admin, catalogue):
    created = await client.post("/api/v1/carts", headers=admin)
    assert created.status_code == 201, created.text
    cart_id = created.json()["id"]
    added = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=admin,
        json={"sku": catalogue["small"], "quantity": 1},
    )
    assert added.status_code == 200, added.text
    return cart_id


# --- opportunities ----------------------------------------------------------


async def test_generation_suggests_the_next_size_up(client, admin, catalogue, cart):
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={"cart_id": cart, "kinds": ["upsell"], "max_suggestions": 50},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    upsold = {i["sku"] for i in body["items"] if i["kind"] == "upsell"}
    assert catalogue["medium"] in upsold
    # Four times the price is a different purchase, not an upgrade.
    assert catalogue["huge"] not in upsold


async def test_generation_suggests_an_add_on_but_not_a_second_main_item(
    client, admin, catalogue, cart
):
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={"cart_id": cart, "kinds": ["cross_sell"], "max_suggestions": 50},
    )
    cross = {i["sku"] for i in response.json()["items"] if i["kind"] == "cross_sell"}
    assert catalogue["addon"] in cross
    assert catalogue["bulky"] not in cross


async def test_generating_twice_stores_one_set_not_two(client, admin, catalogue, cart):
    """The property that makes the endpoint safe to call on every page load."""
    payload = {"cart_id": cart, "max_suggestions": 50}
    first = await client.post("/api/v1/opportunities/generate", headers=admin, json=payload)
    second = await client.post("/api/v1/opportunities/generate", headers=admin, json=payload)

    assert first.json()["items"] == second.json()["items"]

    listed = await client.get("/api/v1/opportunities", headers=admin)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == first.json()["generated"]


async def test_a_stored_opportunity_can_be_read_back(client, admin, catalogue, cart):
    generated = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={"cart_id": cart, "max_suggestions": 50},
    )
    ids = {i["id"] for i in generated.json()["items"]}

    listed = await client.get("/api/v1/opportunities", headers=admin, params={"limit": 200})
    assert ids.issubset({i["id"] for i in listed.json()["items"]})


async def test_listing_can_be_filtered_by_kind(client, admin, catalogue, cart):
    await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={"cart_id": cart, "kinds": ["upsell"], "max_suggestions": 50},
    )
    listed = await client.get(
        "/api/v1/opportunities", headers=admin, params={"kind": "upsell", "limit": 200}
    )
    assert listed.status_code == 200, listed.text
    assert {i["kind"] for i in listed.json()["items"]} == {"upsell"}


async def test_generation_without_a_cart_uses_the_catalogue(client, admin, catalogue):
    response = await client.post(
        "/api/v1/opportunities/generate", headers=admin, json={"max_suggestions": 50}
    )
    assert response.status_code == 201, response.text
    assert response.json()["subject_id"] == f"catalog:{MERCHANT}"


# --- what a recommendation may do -------------------------------------------


async def test_a_recommendation_the_rules_reject_is_reported_not_stored(
    client, admin, catalogue, cart
):
    """The AI may widen the search. It may not overrule the rules."""
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={
            "cart_id": cart,
            "max_suggestions": 50,
            "recommendations": [{"kind": "upsell", "sku": catalogue["huge"]}],
        },
    )
    body = response.json()
    assert catalogue["huge"] not in {i["sku"] for i in body["items"]}
    rejected = {r["sku"]: r["reason"] for r in body["rejected"]}
    assert rejected[catalogue["huge"]] == "does not qualify under the rules"


async def test_a_recommendation_for_an_unknown_sku_is_reported(client, admin, cart):
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={
            "cart_id": cart,
            "recommendations": [{"kind": "cross_sell", "sku": "NO-SUCH-SKU"}],
        },
    )
    rejected = {r["sku"]: r["reason"] for r in response.json()["rejected"]}
    assert rejected["NO-SUCH-SKU"] == "not in this catalogue"


async def test_a_recommendation_cannot_carry_a_price_or_a_score(client, admin, cart, catalogue):
    """Extra fields are dropped by the schema, so the rules still set the number."""
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={
            "cart_id": cart,
            "kinds": ["upsell"],
            "max_suggestions": 50,
            "recommendations": [
                {
                    "kind": "upsell",
                    "sku": catalogue["medium"],
                    "score": 9.9,
                    "list_price_paise": 1,
                    "discount_paise": 999_999,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    suggested = [i for i in response.json()["items"] if i["sku"] == catalogue["medium"]]
    assert suggested, "the recommended sku should still have been suggested by the rules"
    assert suggested[0]["score"] <= 1
    assert suggested[0]["list_price_paise"] == 120_000


async def test_no_opportunity_carries_authority_over_money(client, admin, cart, catalogue):
    """A suggestion is an idea. Funding one costs a campaign reservation."""
    response = await client.post(
        "/api/v1/opportunities/generate",
        headers=admin,
        json={"cart_id": cart, "max_suggestions": 50},
    )
    forbidden = ("discount", "budget", "campaign", "final_amount", "override")
    for item in response.json()["items"]:
        assert not [k for k in item if any(word in k.lower() for word in forbidden)]


# --- campaigns --------------------------------------------------------------


@pytest.fixture
async def campaign(client: AsyncClient, admin):
    response = await client.post(
        "/api/v1/campaigns",
        headers=admin,
        json={"name": "Spring growth", "budget_paise": 100_000, "max_discount_pct": "10.5"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_new_campaign_starts_with_its_whole_budget_available(campaign):
    assert campaign["budget_paise"] == 100_000
    assert campaign["reserved_paise"] == 0
    assert campaign["spent_paise"] == 0
    assert campaign["remaining_paise"] == 100_000
    assert campaign["active"] is True


async def test_a_campaign_appears_in_this_merchants_list(client, admin, campaign):
    listed = await client.get("/api/v1/campaigns", headers=admin)
    assert listed.status_code == 200, listed.text
    assert campaign["id"] in {c["id"] for c in listed.json()["items"]}


async def test_reserving_moves_money_out_of_circulation(client, admin, campaign):
    response = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 60_000, "idempotency_key": f"res-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reserved_paise"] == 60_000
    assert body["budget"]["remaining_paise"] == 40_000
    assert body["budget"]["reserved_paise"] == 60_000
    assert body["budget"]["spent_paise"] == 0


async def test_the_budget_endpoint_agrees_with_the_reservation(client, admin, campaign):
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 25_000, "idempotency_key": f"res-{uuid.uuid4().hex}"},
    )
    budget = await client.get(f"/api/v1/campaigns/{campaign['id']}/budget", headers=admin)
    assert budget.status_code == 200, budget.text
    assert budget.json() == {
        "campaign_id": campaign["id"],
        "budget_paise": 100_000,
        "reserved_paise": 25_000,
        "spent_paise": 0,
        "remaining_paise": 75_000,
        "exhausted": False,
    }


async def test_a_reservation_beyond_the_cap_is_refused(client, admin, campaign):
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 60_000, "idempotency_key": f"res-{uuid.uuid4().hex}"},
    )
    over = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 60_000, "idempotency_key": f"res-{uuid.uuid4().hex}"},
    )
    assert over.status_code == 409, over.text
    assert over.json()["error"]["code"] == "BUDGET_EXCEEDED"

    budget = await client.get(f"/api/v1/campaigns/{campaign['id']}/budget", headers=admin)
    assert budget.json()["reserved_paise"] == 60_000


async def test_exhausting_a_budget_exactly_is_allowed(client, admin, campaign):
    response = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 100_000, "idempotency_key": f"res-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["budget"]["exhausted"] is True


async def test_a_release_returns_the_money(client, admin, campaign):
    key = f"res-{uuid.uuid4().hex}"
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 40_000, "idempotency_key": key},
    )
    released = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/release",
        headers=admin,
        json={"amount_paise": 40_000, "reason": "checkout abandoned"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["budget"]["remaining_paise"] == 100_000


async def test_a_campaign_ledger_records_every_movement(client, admin, campaign):
    """Read from the store, not from the API's own account of itself."""
    key = f"res-{uuid.uuid4().hex}"
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/reserve",
        headers=admin,
        json={"amount_paise": 30_000, "idempotency_key": key},
    )
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/release",
        headers=admin,
        json={"amount_paise": 10_000},
    )

    entries = await CampaignRepository().ledger_for(campaign["id"], merchant_id=MERCHANT)
    assert [(e["entry_type"], e["amount_paise"]) for e in entries] == [
        ("reserve", 30_000),
        ("release", 10_000),
    ]
    assert entries[-1]["balance_after_paise"] == 80_000


async def test_a_campaign_from_another_merchant_is_invisible(client, campaign, jwt_manager):
    """404, not 403. A 403 on a real id confirms the id is real."""
    other = jwt_manager.create_access_token(
        user_id="user_other_admin", merchant_id="merchant_other", role="admin"
    )
    response = await client.get(
        f"/api/v1/campaigns/{campaign['id']}/budget",
        headers={"Authorization": f"Bearer {other}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CAMPAIGN_NOT_FOUND"
