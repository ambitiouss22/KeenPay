"""The tool registry: what it accepts, what it refuses, what it records."""

import httpx
import pytest

from ai_runtime.client import ControlPlaneClient
from ai_runtime.credentials import AgentCredential
from ai_runtime.tools import (
    TOOL_SPECS,
    TOOL_SPECS_BY_NAME,
    ToolAudit,
    ToolNotPermittedError,
    ToolRegistry,
    validate_arguments,
)
from ai_runtime.tools.tool_defs import ADD_TO_CART, REQUEST_AUTHORIZATION, ToolKind


@pytest.fixture
def registry(agent_token, ai_settings, control_plane) -> ToolRegistry:
    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    return ToolRegistry(client, max_calls=ai_settings.max_tool_calls)


# --- what exists -----------------------------------------------------------


def test_the_tool_set_is_exactly_what_is_declared(registry):
    assert set(registry.names) == set(TOOL_SPECS_BY_NAME)
    assert len(TOOL_SPECS) == len(TOOL_SPECS_BY_NAME)


def test_describe_labels_each_tool_read_or_request(registry):
    described = ToolRegistry.describe()
    assert {d["kind"] for d in described} == {"read", "request"}
    by_name = {d["name"]: d for d in described}
    assert by_name["search_products"]["kind"] == "read"
    assert by_name["request_authorization"]["kind"] == "request"


def test_schemas_are_well_formed_for_a_tool_calling_model():
    for schema in ToolRegistry.schemas():
        assert schema["name"]
        assert schema["description"]
        assert schema["input_schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "name", ["capture_payment", "refund_payment", "approve_authorization", "execute_sql"]
)
async def test_money_moving_tools_raise_rather_than_fail_softly(registry, name):
    """Not a recoverable ToolResult: this is the thing the service prevents."""
    with pytest.raises(ToolNotPermittedError, match=name):
        await registry.call(name, {})


async def test_unknown_tool_names_the_available_ones(registry):
    with pytest.raises(ToolNotPermittedError, match="search_products"):
        await registry.call("teleport_money", {})


# --- argument validation ---------------------------------------------------


def test_missing_required_argument_is_named():
    with pytest.raises(ValueError, match="quantity"):
        validate_arguments(ADD_TO_CART, {"cart_id": "c1", "sku": "S1"})


def test_unexpected_argument_is_refused():
    """``price_paise`` is exactly the field an agent must not be able to send."""
    with pytest.raises(ValueError, match="price_paise"):
        validate_arguments(
            ADD_TO_CART,
            {"cart_id": "c1", "sku": "S1", "quantity": 1, "price_paise": 1},
        )


def test_boolean_is_not_an_integer():
    """``True`` is an int in Python; a quantity of True is a bug, not a 1."""
    with pytest.raises(ValueError, match="quantity"):
        validate_arguments(ADD_TO_CART, {"cart_id": "c1", "sku": "S1", "quantity": True})


def test_float_amount_is_refused():
    with pytest.raises(ValueError, match="amount_paise"):
        validate_arguments(REQUEST_AUTHORIZATION, {"order_id": "o1", "amount_paise": 249.9})


def test_quantity_bounds_are_enforced():
    with pytest.raises(ValueError, match="at most"):
        validate_arguments(ADD_TO_CART, {"cart_id": "c1", "sku": "S1", "quantity": 5000})
    with pytest.raises(ValueError, match="at least"):
        validate_arguments(ADD_TO_CART, {"cart_id": "c1", "sku": "S1", "quantity": 0})


def test_defaults_are_applied():
    from ai_runtime.tools.tool_defs import SEARCH_PRODUCTS

    assert validate_arguments(SEARCH_PRODUCTS, {})["limit"] == 10


async def test_bad_arguments_come_back_as_a_result_not_an_exception(registry):
    """A hallucinated argument is normal control flow for an agent."""
    result = await registry.call("add_to_cart", {"cart_id": "c1", "sku": "S1"})
    assert not result.ok
    assert "quantity" in (result.error or "")
    assert registry.invocations[-1]["ok"] is False


# --- dispatch --------------------------------------------------------------


async def test_search_products_is_read_only(registry, control_plane):
    result = await registry.call("search_products", {"query": "tea", "limit": 5})
    assert result.ok
    assert control_plane.requests == [("GET", "/api/v1/products")]


async def test_agent_cannot_name_a_price_or_a_discount(
    agent_token, ai_settings, control_plane
):
    """Asserted on the wire bodies, which is where it would actually go wrong.

    Two fields must never leave this service: a price on a cart line, and a
    discount on a checkout. Either would let the agent decide what the buyer
    pays, which is the merchant's decision and the Control Plane's arithmetic.
    """
    bodies: list[tuple[str, dict]] = []
    inner = control_plane.transport()

    class Recording(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.content:
                import json as _json

                bodies.append((request.url.path, _json.loads(request.content)))
            return await inner.handle_async_request(request)

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=Recording()
        ),
    )
    registry = ToolRegistry(client, max_calls=10)

    cart = await registry.call("create_cart", {})
    cart_id = cart.data["id"]
    await registry.call(
        "add_to_cart", {"cart_id": cart_id, "sku": "TEA-GREEN-100", "quantity": 2}
    )
    await registry.call(
        "checkout_cart", {"cart_id": cart_id, "idempotency_key": "run-abcdefgh"}
    )
    await client.aclose()

    item_body = next(body for path, body in bodies if path.endswith("/items"))
    assert set(item_body) == {"sku", "quantity"}

    checkout_body = next(body for path, body in bodies if path.endswith("/checkout"))
    assert set(checkout_body) == {"idempotency_key"}


async def test_request_authorization_asks_and_does_not_grant(registry, control_plane):
    result = await registry.call(
        "request_authorization", {"order_id": "ord_stub_1", "amount_paise": 24900}
    )
    assert result.ok
    assert ("POST", "/api/v1/authorizations") in control_plane.requests
    assert not control_plane.touched("/payments")
    assert not control_plane.touched("/approve")


async def test_control_plane_error_becomes_a_readable_result(registry):
    result = await registry.call("get_product", {"sku": "NO-SUCH-SKU"})
    assert not result.ok
    assert result.status_code == 404
    assert "Not found" in (result.error or "")


# --- budget and audit ------------------------------------------------------


async def test_call_budget_is_enforced(agent_token, ai_settings, control_plane):
    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    small = ToolRegistry(client, max_calls=2)
    await small.call("search_products", {})
    await small.call("search_products", {})
    third = await small.call("search_products", {})

    assert not third.ok
    assert "budget" in (third.error or "")


async def test_every_invocation_is_recorded(registry):
    await registry.call("search_products", {"query": "tea"})
    await registry.call("get_product", {"sku": "TEA-GREEN-100"})

    audit = ToolAudit(registry.invocations)
    assert audit.tools_used == ["search_products", "get_product"]
    assert audit.used_only_permitted()
    assert audit.request_kind_calls == []


async def test_audit_separates_request_kind_calls(registry):
    await registry.call("search_products", {})
    await registry.call("create_cart", {})

    audit = ToolAudit(registry.invocations)
    assert audit.request_kind_calls == ["create_cart"]
    assert registry.invocations[0]["kind"] == ToolKind.READ.value
