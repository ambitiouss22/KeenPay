"""The allowlisted Control Plane client.

These exercise the real client against a stub transport, so the allowlist,
credential check, path rendering and retry policy are all in the path under
test. Mocking the client would leave every one of them unexercised.
"""

import httpx
import pytest

from ai_runtime.client import (
    ALLOWLIST,
    ALLOWLIST_BY_NAME,
    ControlPlaneClient,
    ControlPlaneError,
    EndpointNotAllowedError,
)
from ai_runtime.credentials import AgentCredential


def _client(token: str, settings, control_plane) -> ControlPlaneClient:
    return ControlPlaneClient(
        credential=AgentCredential.parse(token),
        settings=settings,
        client=httpx.AsyncClient(
            base_url=settings.control_plane_url, transport=control_plane.transport()
        ),
    )


async def test_allowlisted_read_succeeds(agent_token, ai_settings, control_plane):
    client = _client(agent_token, ai_settings, control_plane)
    response = await client.call("list_products", query={"limit": 5})
    await client.aclose()

    assert response.ok
    assert response.body["items"]
    assert control_plane.requests == [("GET", "/api/v1/products")]


async def test_unknown_endpoint_never_reaches_the_network(
    agent_token, ai_settings, control_plane
):
    """The refusal happens before a socket is opened."""
    client = _client(agent_token, ai_settings, control_plane)
    with pytest.raises(EndpointNotAllowedError):
        await client.call("capture_payment", body={"amount_paise": 100})
    await client.aclose()

    assert control_plane.requests == []


@pytest.mark.parametrize(
    "name",
    ["create_payment", "capture_payment", "refund_payment", "approve_authorization"],
)
async def test_money_moving_endpoints_are_absent(
    name, agent_token, ai_settings, control_plane
):
    client = _client(agent_token, ai_settings, control_plane)
    with pytest.raises(EndpointNotAllowedError):
        await client.call(name)
    await client.aclose()
    assert name not in ALLOWLIST_BY_NAME


async def test_path_parameters_are_encoded_not_concatenated(agent_token, ai_settings):
    """A traversal attempt stays inside one path segment.

    Asserted against the raw wire path, because that is what a server routes
    on. Without the encoding, a cart id of ``../../payments`` would leave the
    allowlisted route while still having matched a permitted template - the
    allowlist would be intact and useless.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json={})

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=httpx.MockTransport(handler)
        ),
    )
    await client.call("get_cart", path_params={"cart_id": "../../payments"})
    await client.aclose()

    assert seen["raw"] == "/api/v1/carts/..%2F..%2Fpayments"
    assert not seen["raw"].endswith("/payments")


async def test_missing_path_parameter_is_refused(agent_token, ai_settings, control_plane):
    client = _client(agent_token, ai_settings, control_plane)
    with pytest.raises(EndpointNotAllowedError, match="missing path parameter"):
        await client.call("get_cart")
    await client.aclose()


async def test_unknown_path_parameter_is_refused(agent_token, ai_settings, control_plane):
    client = _client(agent_token, ai_settings, control_plane)
    with pytest.raises(EndpointNotAllowedError, match="no path parameter"):
        await client.call("list_products", path_params={"cart_id": "x"})
    await client.aclose()


async def test_credential_missing_the_endpoint_scope_is_refused(
    make_agent_token, ai_settings, control_plane
):
    """Scope lives on the allowlist entry, so no caller can forget to check it."""
    token = make_agent_token(scopes="catalog:read")
    client = _client(token, ai_settings, control_plane)

    with pytest.raises(ControlPlaneError, match="authorization:request"):
        await client.call("request_authorization", body={"kind": "payment"})
    await client.aclose()

    assert control_plane.requests == []


async def test_expired_credential_is_refused(make_agent_token, ai_settings, control_plane):
    client = _client(make_agent_token(expires_in=-5), ai_settings, control_plane)
    with pytest.raises(ControlPlaneError, match="expired"):
        await client.call("list_products")
    await client.aclose()
    assert control_plane.requests == []


async def test_bearer_token_is_sent(agent_token, ai_settings):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"items": []})

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=httpx.MockTransport(handler)
        ),
    )
    await client.call("list_products")
    await client.aclose()

    assert seen["authorization"] == f"Bearer {agent_token}"
    assert seen["x-agent-runtime"] == "keenpay-ai-runtime"


async def test_no_correlation_id_means_no_header(agent_token, ai_settings, control_plane):
    """The Control Plane generates its own id when none is offered."""
    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=control_plane.transport()
        ),
    )
    await client.call("list_products")
    await client.aclose()
    # httpx lower-cases header names, so assert on the lower-cased key.
    assert "x-request-id" not in control_plane.headers[0]


async def test_the_correlation_id_is_sent_on_every_call(agent_token, ai_settings):
    """One run, one id - otherwise its calls are unrelated lines in the trail."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-request-id"))
        return httpx.Response(200, json={"items": []})

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=httpx.MockTransport(handler)
        ),
        correlation_id="run_abcdef0123456789",
    )
    await client.call("list_products")
    await client.call("get_product", path_params={"sku": "X"})
    await client.aclose()

    assert seen == ["run_abcdef0123456789", "run_abcdef0123456789"]


async def test_writes_are_not_retried(agent_token, ai_settings):
    """A retried POST is how one authorization request becomes two approvals."""
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("boom")

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=httpx.MockTransport(handler)
        ),
    )
    with pytest.raises(ControlPlaneError):
        await client.call("create_cart", body={})
    await client.aclose()

    assert attempts["n"] == 1


async def test_reads_are_retried(agent_token, ai_settings):
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"items": []})

    client = ControlPlaneClient(
        credential=AgentCredential.parse(agent_token),
        settings=ai_settings,
        client=httpx.AsyncClient(
            base_url=ai_settings.control_plane_url, transport=httpx.MockTransport(handler)
        ),
    )
    response = await client.call("list_products")
    await client.aclose()

    assert response.ok
    assert attempts["n"] == 2


async def test_call_log_records_every_call(agent_token, ai_settings, control_plane):
    client = _client(agent_token, ai_settings, control_plane)
    await client.call("list_products")
    await client.call("get_product", path_params={"sku": "TEA-GREEN-100"})
    await client.aclose()

    assert [entry["endpoint"] for entry in client.call_log] == ["list_products", "get_product"]
    assert all(entry["status_code"] == 200 for entry in client.call_log)


def test_allowlist_entries_all_declare_a_scope():
    """An endpoint with no scope requirement would be reachable by any token."""
    for endpoint in ALLOWLIST:
        assert endpoint.required_scopes, f"{endpoint.name} declares no required scope"
