"""End-to-end runs of the AI Runtime, and its own HTTP surface.

The runs go through the real runner, the real graph, the real tool registry and
the real allowlisted client, against a stub Control Plane that records every
request it receives. That recording is what the assertions read: whether the
agent moved money is answered by what arrived at the Control Plane, not by what
the agent says it did.
"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from ai_runtime.agents import AgentRunner
from ai_runtime.credentials import CredentialError
from ai_runtime.main import create_app


@pytest.fixture
def runner(ai_settings, control_plane) -> AgentRunner:
    return AgentRunner(
        settings=ai_settings,
        http_client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )


# --- the happy path --------------------------------------------------------


async def test_purchase_run_reaches_an_authorization_and_stops(
    runner, control_plane, agent_token
):
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    assert report.stage == "report"
    assert report.recommendations
    assert report.cart_id
    assert report.order_id == "ord_stub_1"
    assert report.authorization_id == "authz_stub_1"
    assert report.authorization_status == "approved"

    # The furthest it got was asking.
    assert ("POST", "/api/v1/authorizations") in control_plane.requests
    assert not control_plane.touched("/payments")
    assert not control_plane.touched("/approve")
    assert not control_plane.touched("/refund")
    assert report.money_moved is False
    assert report.guardrail_ok


async def test_the_reply_states_that_nothing_was_charged(runner, agent_token):
    report = await runner.run(message="buy me green tea", agent_token=agent_token)
    assert "Nothing has been charged" in report.reply


async def test_pending_authorization_is_reported_as_awaiting_a_human(
    ai_settings, control_plane, agent_token
):
    control_plane.authorization_status = "pending"
    runner = AgentRunner(
        settings=ai_settings,
        http_client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    assert report.authorization_status == "pending"
    assert "human approval" in report.reply
    assert not control_plane.touched("/payments")


async def test_denied_authorization_is_not_retried(
    ai_settings, control_plane, agent_token
):
    """A denial is an answer. Retrying it with a different shape is gaming the gate."""
    control_plane.authorization_status = "denied"
    runner = AgentRunner(
        settings=ai_settings,
        http_client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    assert report.authorization_status == "denied"
    assert control_plane.paths.count("/api/v1/authorizations") == 1
    assert "denied" in report.reply


async def test_browsing_never_opens_a_cart(runner, control_plane, agent_token):
    report = await runner.run(message="what green tea do you have", agent_token=agent_token)

    assert report.recommendations
    assert report.cart_id is None
    assert report.order_id is None
    assert not control_plane.touched("/carts")
    assert not control_plane.touched("/authorizations")


async def test_budget_is_honoured_end_to_end(runner, control_plane, agent_token):
    """The reserve tea costs ₹8,990 and must not be proposed under a ₹500 budget."""
    report = await runner.run(
        message="buy me green tea under 500", agent_token=agent_token
    )

    assert report.recommendations
    assert all(r["line_total_paise"] <= 50000 for r in report.recommendations)
    assert "TEA-RARE-500" not in [r["sku"] for r in report.recommendations]


async def test_no_match_reports_honestly_without_buying_something_else(
    runner, control_plane, agent_token
):
    report = await runner.run(
        message="buy me a helicopter under 100", agent_token=agent_token
    )

    assert report.recommendations == []
    assert report.order_id is None
    assert not control_plane.touched("/carts")
    assert "couldn't find" in report.reply


async def test_runtime_ceiling_stops_an_oversized_request(
    ai_settings, control_plane, agent_token
):
    ai_settings.max_request_amount_paise = 10000
    runner = AgentRunner(
        settings=ai_settings,
        http_client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    assert report.authorization_id is None
    assert not control_plane.touched("/authorizations")
    assert any("ceiling" in e for e in report.errors)


async def test_every_call_in_a_run_carries_the_run_id(runner, control_plane, agent_token):
    """One run is one traceable unit in the Control Plane's own audit trail."""
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    sent = {headers.get("x-request-id") for headers in control_plane.headers}
    assert sent == {report.run_id}


async def test_the_run_report_lists_every_control_plane_call(
    runner, control_plane, agent_token
):
    """The report is evidence, so it must match what the far end actually saw."""
    report = await runner.run(message="buy me green tea", agent_token=agent_token)

    assert [c["endpoint"] for c in report.control_plane_calls] == [
        "list_products",
        "create_cart",
        "add_cart_item",
        "checkout_cart",
        "request_authorization",
    ]
    assert len(report.control_plane_calls) == len(control_plane.requests)


async def test_a_retried_run_sends_the_same_idempotency_key(
    runner, agent_token, control_plane
):
    """A retry of one run must reach the same order, not create a second one."""
    await runner.run(message="buy me green tea", agent_token=agent_token, run_id="run_fixed")
    await runner.run(message="buy me green tea", agent_token=agent_token, run_id="run_fixed")

    keys = [
        body["idempotency_key"] for path, body in control_plane.bodies if "checkout" in path
    ]
    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert len(keys[0]) >= 8


async def test_different_runs_do_not_share_an_idempotency_key(
    runner, agent_token, control_plane
):
    await runner.run(message="buy me green tea", agent_token=agent_token)
    await runner.run(message="buy me green tea", agent_token=agent_token)

    keys = [
        body["idempotency_key"] for path, body in control_plane.bodies if "checkout" in path
    ]
    assert keys[0] != keys[1]


# --- credentials -----------------------------------------------------------


async def test_a_run_without_a_credential_is_refused(runner, control_plane):
    with pytest.raises(CredentialError, match="no agent credential"):
        await runner.run(message="buy me green tea")
    assert control_plane.requests == []


async def test_a_wrongly_aimed_credential_is_refused(
    runner, control_plane, make_agent_token
):
    with pytest.raises(CredentialError, match="audience"):
        await runner.run(
            message="buy me green tea",
            agent_token=make_agent_token(audience="some-other-service"),
        )
    assert control_plane.requests == []


async def test_a_catalogue_only_credential_cannot_request_an_authorization(
    ai_settings, control_plane, make_agent_token
):
    """Least privilege, observed: it can shop, and it cannot ask for money."""
    runner = AgentRunner(
        settings=ai_settings,
        http_client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    report = await runner.run(
        message="buy me green tea",
        agent_token=make_agent_token(scopes="catalog:read session:create"),
    )

    assert report.authorization_id is None
    assert not control_plane.touched("/authorizations")
    assert any("authorization:request" in e for e in report.errors)


# --- the runtime's own HTTP surface ---------------------------------------


@pytest.fixture
async def ai_client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://ai-runtime.test") as ac:
        yield ac


async def test_liveness(ai_client):
    response = await ai_client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_the_isolation_verdict(ai_client):
    response = await ai_client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["isolated"] is True
    assert body["violations"] == []
    assert body["graph_engine"] in {"langgraph", "sequential"}


async def test_tool_listing_publishes_the_whole_capability_set(ai_client):
    """An operator can audit the agent's power without reading the code."""
    response = await ai_client.get("/agent/tools")
    assert response.status_code == 200
    body = response.json()

    names = {t["name"] for t in body["tools"]}
    assert "search_products" in names
    assert "request_authorization" in names
    assert names.isdisjoint(set(body["forbidden"]))

    for endpoint in body["allowlisted_endpoints"]:
        assert "/payments" not in endpoint


async def test_run_without_a_token_is_a_401_not_a_500(ai_client):
    response = await ai_client.post("/agent/run", json={"message": "buy green tea"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AGENT_CREDENTIAL_REJECTED"


async def test_run_rejects_an_unknown_body_field(ai_client, agent_token):
    """``merchant_id`` in a body would let a caller shop someone else's catalogue."""
    response = await ai_client.post(
        "/agent/run",
        json={"message": "buy green tea", "merchant_id": "merchant_other"},
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert response.status_code == 422
