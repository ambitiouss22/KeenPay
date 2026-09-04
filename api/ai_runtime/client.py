"""The only way out of this service: an allowlisted Control Plane client.

Every rule about what the AI may do reduces to a rule about what HTTP requests
it may send, so that is where the enforcement lives. ``ALLOWLIST`` is the whole
list. A request whose method and path template are not in it raises
:class:`EndpointNotAllowedError` *before* any socket is opened - there is no
generic ``get()`` or ``post()`` on this class to slip through.

Three consequences worth stating.

**Adding a capability is a diff to a visible list.** Giving the agent the
ability to capture a payment would mean adding ``POST /api/v1/payments`` here,
in a file whose tests assert exactly which entries exist. It cannot happen as a
side effect of a prompt change, a model upgrade, or a new tool.

**Path parameters are substituted, never concatenated.** The template is
matched first and formatted second, so a cart id of ``../../payments`` produces
a quoted path segment rather than a different endpoint.

**The credential is checked before it is sent.** Scope requirements sit on the
allowlist entry, next to the endpoint they protect, so the check cannot be
forgotten by a caller that forgot to ask for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ai_runtime.config import AIRuntimeSettings, get_ai_settings
from ai_runtime.credentials import AgentCredential, CredentialError

#: Scope names. They mirror the Control Plane's permissions, but the agent is
#: given a strict subset: read the catalogue, build a cart, ask for money to
#: move. Nothing here can approve, capture or refund.
SCOPE_CATALOG_READ = "catalog:read"
SCOPE_CART_WRITE = "session:create"
SCOPE_ORDER_READ = "order:read:own"
SCOPE_AUTHORIZATION_REQUEST = "authorization:request"
SCOPE_AUTHORIZATION_READ = "authorization:read"

AGENT_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_CATALOG_READ,
        SCOPE_CART_WRITE,
        SCOPE_ORDER_READ,
        SCOPE_AUTHORIZATION_REQUEST,
        SCOPE_AUTHORIZATION_READ,
    }
)


class ControlPlaneError(RuntimeError):
    """A Control Plane call failed. Carries the status and parsed body."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class EndpointNotAllowedError(ControlPlaneError):
    """The runtime tried to call an endpoint that is not on the allowlist.

    A programming error, not a runtime condition: it means code in this service
    asked for a capability the service is not supposed to have.
    """


@dataclass(frozen=True)
class Endpoint:
    """One permitted call, with the scope it requires."""

    name: str
    method: str
    path_template: str
    required_scopes: frozenset[str] = frozenset()
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.method} {self.path_template}"


#: Everything this service may ever ask the Control Plane to do.
#:
#: Read it as the agent's complete power. Notice what is absent: no
#: ``POST /api/v1/payments``, no refund, no approve, no product write, no
#: admin route. The agent can look at a catalogue, assemble a cart, turn it
#: into a pending order, ask for an authorization, and read back what it asked
#: for. Money moves only when a human or the Control Plane's own rules say so.
ALLOWLIST: tuple[Endpoint, ...] = (
    Endpoint(
        name="list_products",
        method="GET",
        path_template="/api/v1/products",
        required_scopes=frozenset({SCOPE_CATALOG_READ}),
        description="Browse the merchant catalogue.",
    ),
    Endpoint(
        name="get_product",
        method="GET",
        path_template="/api/v1/products/{sku}",
        required_scopes=frozenset({SCOPE_CATALOG_READ}),
        description="Read one product by sku.",
    ),
    Endpoint(
        name="create_cart",
        method="POST",
        path_template="/api/v1/carts",
        required_scopes=frozenset({SCOPE_CART_WRITE}),
        description="Open a cart for the credential's own buyer.",
    ),
    Endpoint(
        name="get_cart",
        method="GET",
        path_template="/api/v1/carts/{cart_id}",
        required_scopes=frozenset({SCOPE_CART_WRITE}),
        description="Read a cart the agent opened.",
    ),
    Endpoint(
        name="add_cart_item",
        method="POST",
        path_template="/api/v1/carts/{cart_id}/items",
        required_scopes=frozenset({SCOPE_CART_WRITE}),
        description="Add a line. Price comes from the catalogue, never the agent.",
    ),
    Endpoint(
        name="remove_cart_item",
        method="DELETE",
        path_template="/api/v1/carts/{cart_id}/items/{item_id}",
        required_scopes=frozenset({SCOPE_CART_WRITE}),
        description="Remove a line from a cart the agent opened.",
    ),
    Endpoint(
        name="checkout_cart",
        method="POST",
        path_template="/api/v1/carts/{cart_id}/checkout",
        required_scopes=frozenset({SCOPE_CART_WRITE}),
        description="Turn a cart into a pending order. Takes no money.",
    ),
    Endpoint(
        name="request_authorization",
        method="POST",
        path_template="/api/v1/authorizations",
        required_scopes=frozenset({SCOPE_AUTHORIZATION_REQUEST}),
        description="Ask for permission to move money. Never grants it.",
    ),
    Endpoint(
        name="get_authorization",
        method="GET",
        path_template="/api/v1/authorizations/{authorization_id}",
        required_scopes=frozenset({SCOPE_AUTHORIZATION_READ}),
        description="Read an authorization the agent requested.",
    ),
)

ALLOWLIST_BY_NAME: dict[str, Endpoint] = {e.name: e for e in ALLOWLIST}
ALLOWLIST_KEYS: frozenset[str] = frozenset(e.key for e in ALLOWLIST)


def _render_path(endpoint: Endpoint, params: dict[str, Any] | None) -> str:
    """Fill a template's placeholders, percent-encoding each value.

    ``quote(..., safe="")`` is what stops a path parameter from becoming a path.
    A cart id of ``x/../payments`` encodes to a single segment; without it the
    request would leave the allowlisted route entirely while still having
    matched a permitted template.
    """
    params = params or {}
    rendered = endpoint.path_template
    for key, value in params.items():
        placeholder = "{" + key + "}"
        if placeholder not in rendered:
            raise EndpointNotAllowedError(
                f"endpoint {endpoint.name!r} has no path parameter {key!r}"
            )
        rendered = rendered.replace(placeholder, quote(str(value), safe=""))

    if "{" in rendered:
        missing = rendered[rendered.index("{") :].split("}")[0].lstrip("{")
        raise EndpointNotAllowedError(
            f"endpoint {endpoint.name!r} is missing path parameter {missing!r}"
        )
    return rendered


@dataclass
class ControlPlaneResponse:
    """A response, already narrowed to what a tool needs."""

    status_code: int
    body: Any
    endpoint: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class ControlPlaneClient:
    """Typed, allowlisted, credential-checked HTTP access to the Control Plane.

    Constructed per run so the credential's lifetime matches the run's. The
    transport may be injected, which is how tests exercise the real client
    against a stub Control Plane rather than mocking the client away and
    testing nothing.
    """

    def __init__(
        self,
        *,
        credential: AgentCredential,
        settings: AIRuntimeSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_ai_settings()
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.control_plane_url.rstrip("/"),
            timeout=self._settings.control_plane_timeout_seconds,
            transport=transport,
        )
        #: Every call made through this instance, in order. The run report
        #: carries it, which is what makes "the agent never called payments"
        #: an assertion about evidence rather than about intent.
        self.call_log: list[dict[str, Any]] = []

    async def __aenter__(self) -> ControlPlaneClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def credential(self) -> AgentCredential:
        return self._credential

    def _authorize(self, endpoint: Endpoint) -> None:
        try:
            self._credential.check(
                audience=self._settings.agent_audience,
                required_scopes=endpoint.required_scopes,
            )
        except CredentialError as exc:
            raise ControlPlaneError(
                f"credential rejected for {endpoint.name!r}: {exc}", status_code=401
            ) from exc

    async def call(
        self,
        endpoint_name: str,
        *,
        path_params: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ControlPlaneResponse:
        """Make one allowlisted call.

        Raises :class:`EndpointNotAllowedError` for an unknown endpoint name.
        That is the single choke point: a tool cannot reach the network any
        other way, so a capability the allowlist does not name does not exist
        for this service.
        """
        endpoint = ALLOWLIST_BY_NAME.get(endpoint_name)
        if endpoint is None:
            raise EndpointNotAllowedError(
                f"endpoint {endpoint_name!r} is not on the AI Runtime allowlist"
            )

        self._authorize(endpoint)
        path = _render_path(endpoint, path_params)

        headers = {
            "Authorization": f"Bearer {self._credential.token}",
            "Accept": "application/json",
            # Names the caller in the Control Plane's own logs. An agent-driven
            # request is worth being able to pick out of an audit trail later.
            "X-Agent-Runtime": "keenpay-ai-runtime",
        }

        attempts = max(1, self._settings.control_plane_max_retries + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    endpoint.method,
                    path,
                    params=query,
                    json=body,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                # Retry only transport failures, and only for reads. Retrying a
                # POST that may have been received is how one cart becomes two
                # carts, or one authorization request becomes two pending
                # approvals in a human's queue.
                if endpoint.method != "GET" or attempt == attempts - 1:
                    self._log(endpoint, path, None, error=str(exc))
                    raise ControlPlaneError(
                        f"{endpoint.name} transport error: {exc}"
                    ) from exc
                continue

            parsed = self._parse(response)
            self._log(endpoint, path, response.status_code)
            return ControlPlaneResponse(
                status_code=response.status_code, body=parsed, endpoint=endpoint.name
            )

        raise ControlPlaneError(f"{endpoint.name} failed: {last_exc}")  # pragma: no cover

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            # Truncated: an HTML error page from a proxy is not worth carrying
            # whole into a tool result the model will read.
            return {"raw": response.text[:500]}

    def _log(
        self,
        endpoint: Endpoint,
        path: str,
        status_code: int | None,
        *,
        error: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "endpoint": endpoint.name,
            "method": endpoint.method,
            "path": path,
            "status_code": status_code,
        }
        if error:
            entry["error"] = error
        self.call_log.append(entry)


__all__ = [
    "AGENT_SCOPES",
    "ALLOWLIST",
    "ALLOWLIST_BY_NAME",
    "ALLOWLIST_KEYS",
    "SCOPE_AUTHORIZATION_READ",
    "SCOPE_AUTHORIZATION_REQUEST",
    "SCOPE_CART_WRITE",
    "SCOPE_CATALOG_READ",
    "SCOPE_ORDER_READ",
    "ControlPlaneClient",
    "ControlPlaneError",
    "ControlPlaneResponse",
    "Endpoint",
    "EndpointNotAllowedError",
]
