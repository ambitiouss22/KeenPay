"""Cart to order, through the real HTTP surface.

Covers the Phase 4 acceptance: a merchant can list products, build a cart and
create an order, all scoped to its own tenant - plus the ways that flow can be
abused.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.jwt import JWTManager
from repositories.carts import reset_carts

pytestmark = pytest.mark.asyncio

PASSWORD = "KeenPayDev1!"


@pytest.fixture(autouse=True)
def _clean_carts():
    """Carts live in a module-level dict, so tests would otherwise see
    each other's state and pass or fail depending on ordering."""
    reset_carts()
    yield
    reset_carts()


@pytest.fixture
def app():
    from main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _login(client, email):
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "merchant_id": "merchant_keen"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def admin(client):
    return await _login(client, "admin@keenpay.dev")


@pytest.fixture
async def shopper(client):
    return await _login(client, "shopper@keenpay.dev")


@pytest.fixture
async def product(client, admin):
    """A product unique to this test, so tests do not fight over stock."""
    sku = f"T-{uuid.uuid4().hex[:8].upper()}"
    r = await client.post(
        "/api/v1/products",
        headers=admin,
        json={
            "sku": sku,
            "name": "Test Item",
            "list_price_paise": 25000,
            "cost_paise": 10000,
            "quantity_on_hand": 10,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def new_cart(client, headers) -> str:
    r = await client.post("/api/v1/carts", headers=headers, json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- catalogue --------------------------------------------------------------


async def test_merchant_can_list_products(client, shopper, product):
    r = await client.get("/api/v1/products", headers=shopper)
    assert r.status_code == 200
    assert any(p["sku"] == product["sku"] for p in r.json()["items"])


async def test_creating_a_product_requires_authority(client, shopper):
    r = await client.post(
        "/api/v1/products",
        headers=shopper,
        json={"sku": "X1", "name": "n", "list_price_paise": 1, "cost_paise": 1},
    )
    assert r.status_code == 403


async def test_duplicate_sku_is_a_conflict(client, admin, product):
    r = await client.post(
        "/api/v1/products",
        headers=admin,
        json={
            "sku": product["sku"],
            "name": "again",
            "list_price_paise": 1,
            "cost_paise": 1,
        },
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SKU_EXISTS"


async def test_float_price_is_rejected_at_the_edge(client, admin):
    r = await client.post(
        "/api/v1/products",
        headers=admin,
        json={"sku": "F1", "name": "f", "list_price_paise": 249.9, "cost_paise": 1},
    )
    assert r.status_code == 422


# --- cart arithmetic --------------------------------------------------------


async def test_cart_starts_empty(client, shopper):
    cart_id = await new_cart(client, shopper)
    r = await client.get(f"/api/v1/carts/{cart_id}", headers=shopper)
    assert r.status_code == 200
    assert r.json()["subtotal_paise"] == 0
    assert r.json()["items"] == []


async def test_adding_items_computes_the_subtotal(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 3},
    )
    assert r.status_code == 200
    assert r.json()["subtotal_paise"] == 75000  # 3 x 25000


async def test_same_sku_merges_into_one_line(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    for _ in range(2):
        r = await client.post(
            f"/api/v1/carts/{cart_id}/items",
            headers=shopper,
            json={"sku": product["sku"], "quantity": 2},
        )
    assert r.json()["line_count"] == 1
    assert r.json()["item_count"] == 4


async def test_price_comes_from_the_catalogue_not_the_request(client, shopper, product):
    """A body field naming a price must not be honoured."""
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1, "unit_price_paise": 1},
    )
    assert r.status_code == 200
    assert r.json()["subtotal_paise"] == 25000, "the catalogue price must win"


async def test_removing_an_item_updates_totals(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 2},
    )
    item_id = r.json()["items"][0]["item_id"]
    r = await client.delete(f"/api/v1/carts/{cart_id}/items/{item_id}", headers=shopper)
    assert r.status_code == 200
    assert r.json()["subtotal_paise"] == 0


async def test_removing_an_unknown_item_is_404(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.delete(f"/api/v1/carts/{cart_id}/items/item_nope", headers=shopper)
    assert r.status_code == 404


@pytest.mark.parametrize("qty", [0, -1, 5000])
async def test_bad_quantities_are_refused(client, shopper, product, qty):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": qty},
    )
    assert r.status_code == 422


async def test_cannot_add_more_than_stock(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 11},  # stock is 10
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_STOCK"


async def test_stock_is_checked_against_the_cart_total_not_the_increment(
    client, shopper, product
):
    """Adding 6 twice against a stock of 10 must fail on the second call."""
    cart_id = await new_cart(client, shopper)
    first = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 6},
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 6},
    )
    assert second.status_code == 422


async def test_unknown_sku_is_404(client, shopper):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": "NO-SUCH-SKU", "quantity": 1},
    )
    assert r.status_code == 404


# --- checkout ---------------------------------------------------------------


async def test_cart_becomes_a_pending_order(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 2},
    )
    r = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh"},
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["subtotal_paise"] == 50000
    assert order["final_amount_paise"] == 50000
    assert order["status"] == "pending", "checkout must not take payment"


async def test_discount_is_applied(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 2},
    )
    r = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh", "discount_paise": 10000},
    )
    assert r.json()["final_amount_paise"] == 40000


async def test_discount_larger_than_the_cart_is_refused(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1},
    )
    r = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh", "discount_paise": 999_999},
    )
    assert r.status_code == 422


async def test_empty_cart_cannot_be_checked_out(client, shopper):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CART_EMPTY"


async def test_double_checkout_is_a_conflict_not_a_second_order(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1},
    )
    first = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh"},
    )
    second = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-ijklmnop"},
    )
    assert first.status_code == 201
    assert second.status_code == 409


async def test_a_checked_out_cart_is_closed_to_edits(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1},
    )
    await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=shopper,
        json={"idempotency_key": "key-abcdefgh"},
    )
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1},
    )
    assert r.status_code == 409


# --- tenant isolation -------------------------------------------------------


def foreign(role: str = "shopper") -> dict:
    token = JWTManager().create_access_token(
        user_id="mallory", merchant_id="merchant_acme", role=role
    )
    return {"Authorization": f"Bearer {token}"}


async def test_another_merchant_cannot_read_the_cart(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.get(f"/api/v1/carts/{cart_id}", headers=foreign())
    assert r.status_code == 404


async def test_another_merchant_cannot_add_to_the_cart(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    r = await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=foreign(),
        json={"sku": product["sku"], "quantity": 1},
    )
    assert r.status_code == 404


async def test_another_merchant_cannot_check_the_cart_out(client, shopper, product):
    cart_id = await new_cart(client, shopper)
    await client.post(
        f"/api/v1/carts/{cart_id}/items",
        headers=shopper,
        json={"sku": product["sku"], "quantity": 1},
    )
    r = await client.post(
        f"/api/v1/carts/{cart_id}/checkout",
        headers=foreign(),
        json={"idempotency_key": "steal-it-now"},
    )
    assert r.status_code == 404


async def test_an_admin_of_another_merchant_is_still_refused(client, shopper):
    cart_id = await new_cart(client, shopper)
    r = await client.get(f"/api/v1/carts/{cart_id}", headers=foreign(role="admin"))
    assert r.status_code == 404


async def test_a_foreign_merchant_cannot_see_our_products(client, product):
    r = await client.get(f"/api/v1/products/{product['sku']}", headers=foreign())
    assert r.status_code == 404


async def test_carts_require_authentication(client):
    assert (await client.post("/api/v1/carts", json={})).status_code == 401
    assert (await client.get("/api/v1/carts/cart_x")).status_code == 401
