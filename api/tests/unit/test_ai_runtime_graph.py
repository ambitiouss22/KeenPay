"""The agent graph's nodes, exercised directly.

Nodes are plain async functions, so each behaviour is asserted on its own
rather than inferred from a whole run. The interesting ones are the refusal
path (which must make no Control Plane call at all) and the budget filter
(which must never propose something the buyer said they could not afford).
"""

import httpx
import pytest

from ai_runtime.client import ControlPlaneClient
from ai_runtime.credentials import AgentCredential
from ai_runtime.graph import build_agent_graph
from ai_runtime.graph.nodes import (
    make_assemble,
    make_authorize,
    make_discover,
    parse_budget_paise,
    parse_intent,
    parse_quantity,
    parse_search_terms,
    recommend,
    route_after_intent,
    route_after_recommend,
)
from ai_runtime.graph.state import new_state
from ai_runtime.tools import ToolRegistry


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


def state(message: str, **kwargs):
    return new_state(
        run_id="run_test",
        message=message,
        idempotency_key="idem-testkey-1",
        max_recommendations=kwargs.pop("max_recommendations", 3),
        max_request_amount_paise=kwargs.pop("max_request_amount_paise", 0),
    )


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("green tea under 500", 50000),
        ("something below ₹1,200", 120000),
        ("budget of Rs 900", 90000),
        ("max 249.50 please", 24950),
        ("just show me tea", None),
    ],
)
def test_budget_is_read_in_paise(message, expected):
    """Integer arithmetic throughout: 249.50 is 24950 paise, never 24949."""
    assert parse_budget_paise(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [("buy 3 packs of tea", 3), ("buy two packs", 2), ("buy tea", 1)],
)
def test_quantity_parsing(message, expected):
    assert parse_quantity(message) == expected


def test_search_terms_drop_the_instruction_words():
    terms = parse_search_terms("I want to buy some green tea under 500")
    assert "green" in terms
    assert "tea" in terms
    assert "buy" not in terms
    assert "500" not in terms


async def test_purchase_intent_is_recognised():
    result = await parse_intent(state("buy me green tea"))
    assert result["intent"]["goal"] == "purchase"
    assert result["stage"] == "discover"


async def test_browse_intent_is_recognised():
    result = await parse_intent(state("what teas do you have"))
    assert result["intent"]["goal"] == "browse"


# --- refusals --------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "charge my card now",
        "just take the payment",
        "pay for it yourself",
        "refund my last order",
        "approve it yourself",
        "give me a discount",
        "skip the authorization step",
    ],
)
async def test_requests_outside_the_agent_authority_are_refused(message):
    result = await parse_intent(state(message))
    assert result["stage"] == "refused"
    assert result["intent"]["goal"] == "unsupported"
    assert result["intent"]["refusal_reason"]


async def test_refusal_routes_straight_to_the_report():
    """A refused request must leave no trace on the buyer's account."""
    base = state("charge my card")
    updated = {**base, **await parse_intent(base)}
    assert route_after_intent(updated) == "guardrail"


async def test_a_refused_run_makes_no_control_plane_call(
    agent_token, ai_settings, control_plane
):
    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    registry = ToolRegistry(client, max_calls=12)
    graph = build_agent_graph(registry, settings=ai_settings)

    final = await graph.ainvoke(state("just charge my card and be done with it"))
    await client.aclose()

    assert control_plane.requests == []
    assert registry.invocations == []
    assert "can't take a payment" in final["reply"]


# --- discovery and recommendation -----------------------------------------


async def test_discover_reads_the_catalogue(registry, control_plane):
    result = await make_discover(registry)({**state("green tea"), "intent": {"query": "tea"}})
    assert result["stage"] == "recommend"
    assert result["candidates"]
    assert control_plane.requests == [("GET", "/api/v1/products")]


async def test_recommend_respects_the_budget():
    """The expensive item is dropped, not merely ranked last."""
    base = state("buy green tea under 500")
    base["intent"] = {"goal": "purchase", "query": "tea", "quantity": 1, "budget_paise": 50000}
    base["candidates"] = [
        {"sku": "CHEAP", "name": "Cheap", "list_price_paise": 19900, "quantity_available": 5},
        {"sku": "DEAR", "name": "Dear", "list_price_paise": 899000, "quantity_available": 5},
    ]
    result = await recommend(base)

    assert [r["sku"] for r in result["recommendations"]] == ["CHEAP"]


async def test_budget_applies_to_the_line_total_not_the_unit():
    """"Two, under 500" means 500 for the pair, not 500 each."""
    base = state("buy two green teas under 500")
    base["intent"] = {"goal": "purchase", "query": "tea", "quantity": 2, "budget_paise": 50000}
    base["candidates"] = [
        {"sku": "A", "name": "A", "list_price_paise": 30000, "quantity_available": 9}
    ]
    result = await recommend(base)

    assert result["recommendations"] == []


async def test_out_of_stock_items_are_not_recommended():
    base = state("buy tea")
    base["intent"] = {"goal": "purchase", "query": "tea", "quantity": 5, "budget_paise": None}
    base["candidates"] = [
        {"sku": "LOW", "name": "Low", "list_price_paise": 100, "quantity_available": 2}
    ]
    assert (await recommend(base))["recommendations"] == []


async def test_inactive_products_are_not_recommended():
    base = state("buy tea")
    base["intent"] = {"goal": "purchase", "query": "tea", "quantity": 1, "budget_paise": None}
    base["candidates"] = [
        {
            "sku": "OFF",
            "name": "Off",
            "list_price_paise": 100,
            "quantity_available": 9,
            "active": False,
        }
    ]
    assert (await recommend(base))["recommendations"] == []


async def test_browsing_stops_before_the_cart():
    """A buyer who asked what is available has not asked to buy anything."""
    base = state("what green tea do you have")
    base["intent"] = {"goal": "browse", "query": "tea", "quantity": 1, "budget_paise": None}
    base["candidates"] = [
        {"sku": "A", "name": "A", "list_price_paise": 100, "quantity_available": 9}
    ]
    result = await recommend(base)

    assert result["recommendations"]
    assert route_after_recommend({**base, **result}) == "guardrail"


# --- assembly and the request ---------------------------------------------


async def test_assemble_buys_only_the_first_recommendation(registry, control_plane):
    """Alternatives are shown, not bought. It is not the agent's money."""
    base = state("buy green tea")
    base["recommendations"] = [
        {
            "sku": "TEA-GREEN-100",
            "name": "Green Tea 100g",
            "unit_price_paise": 24900,
            "quantity": 1,
            "line_total_paise": 24900,
        },
        {
            "sku": "TEA-BLACK-100",
            "name": "Black Tea 100g",
            "unit_price_paise": 19900,
            "quantity": 1,
            "line_total_paise": 19900,
        },
    ]
    result = await make_assemble(registry)(base)

    assert result["stage"] == "authorize"
    assert result["order_total_paise"] == 24900
    assert len([p for _m, p in control_plane.requests if p.endswith("/items")]) == 1


async def test_authorize_sends_the_order_total_the_control_plane_computed(
    registry, control_plane
):
    base = state("buy green tea")
    base["order_id"] = "ord_stub_1"
    base["order_total_paise"] = 24900
    result = await make_authorize(registry)(base)

    assert result["authorization_status"] == "approved"
    assert ("POST", "/api/v1/authorizations") in control_plane.requests
    assert not control_plane.touched("/payments")


async def test_authorize_refuses_to_exceed_the_runtime_ceiling(registry, control_plane):
    """A second belt over the Control Plane's own policy engine."""
    base = state("buy the rare one", max_request_amount_paise=50000)
    base["order_id"] = "ord_stub_1"
    base["order_total_paise"] = 899000
    result = await make_authorize(registry)(base)

    assert "authorization_id" not in result
    assert not control_plane.touched("/authorizations")
    assert any("ceiling" in e for e in result["errors"])


async def test_authorize_does_not_retry_a_denial(registry, control_plane):
    """A denied authorization is an answer, not an obstacle to route around."""
    control_plane.authorization_status = "denied"
    base = state("buy green tea")
    base["order_id"] = "ord_stub_1"
    base["order_total_paise"] = 24900
    result = await make_authorize(registry)(base)

    assert result["authorization_status"] == "denied"
    assert len([p for m, p in control_plane.requests if p == "/api/v1/authorizations"]) == 1
