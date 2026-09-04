"""Tool declarations: name, argument schema, and — the important part — kind.

Every tool is either ``READ`` or ``REQUEST``. There is no third kind, and the
absence is the design:

``READ``     looks at something. Cannot change any state anywhere.
``REQUEST``  asks the Control Plane to record an intention - open a cart, add a
             line, create a pending order, ask for an authorization. Each is
             reversible, none of them moves money, and each is re-decided by
             the Control Plane's own rules on arrival.

A tool that captures a payment, refunds one, or approves an authorization would
be a third kind, and none exists. :data:`FORBIDDEN_TOOL_NAMES` lists the names
that must never appear, and :func:`assert_no_forbidden_tools` is run by the
registry at construction, so the guarantee is checked rather than remembered.

The JSON schemas here are the model-facing contract. They are also validated
against in :mod:`ai_runtime.tools` before any call goes out, so a hallucinated
argument becomes a typed tool error the graph can recover from rather than a
malformed HTTP request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ai_runtime.client import (
    SCOPE_AUTHORIZATION_READ,
    SCOPE_AUTHORIZATION_REQUEST,
    SCOPE_CART_WRITE,
    SCOPE_CATALOG_READ,
)
from ai_runtime.isolation import FORBIDDEN_TOOL_NAMES


class ToolKind(str, Enum):
    """What a tool is allowed to do to the world."""

    READ = "read"
    REQUEST = "request"


@dataclass(frozen=True)
class ToolSpec:
    """One tool, as the model sees it and as the runtime enforces it."""

    name: str
    kind: ToolKind
    endpoint: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    required_scopes: frozenset[str] = frozenset()

    def to_schema(self) -> dict[str, Any]:
        """Function schema in the shape a tool-calling model expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


SEARCH_PRODUCTS = ToolSpec(
    name="search_products",
    kind=ToolKind.READ,
    endpoint="list_products",
    description=(
        "Search the merchant's catalogue. Returns products with prices in integer "
        "paise. Read-only: this cannot change stock, price or anything else."
    ),
    parameters={
        "query": {"type": "string", "description": "Free-text search over product names."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    },
    required_scopes=frozenset({SCOPE_CATALOG_READ}),
)

GET_PRODUCT = ToolSpec(
    name="get_product",
    kind=ToolKind.READ,
    endpoint="get_product",
    description="Read one product by its sku, including price and availability.",
    parameters={"sku": {"type": "string", "description": "The product sku."}},
    required=("sku",),
    required_scopes=frozenset({SCOPE_CATALOG_READ}),
)

CREATE_CART = ToolSpec(
    name="create_cart",
    kind=ToolKind.REQUEST,
    endpoint="create_cart",
    description=(
        "Open an empty cart for the buyer this run is acting for. Takes no arguments: "
        "the buyer and merchant come from the credential, not from the agent."
    ),
    required_scopes=frozenset({SCOPE_CART_WRITE}),
)

ADD_TO_CART = ToolSpec(
    name="add_to_cart",
    kind=ToolKind.REQUEST,
    endpoint="add_cart_item",
    description=(
        "Add a quantity of one sku to a cart. There is no price argument on purpose - "
        "the Control Plane prices every line from the catalogue, so the agent cannot "
        "name what the buyer pays."
    ),
    parameters={
        "cart_id": {"type": "string"},
        "sku": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    required=("cart_id", "sku", "quantity"),
    required_scopes=frozenset({SCOPE_CART_WRITE}),
)

VIEW_CART = ToolSpec(
    name="view_cart",
    kind=ToolKind.READ,
    endpoint="get_cart",
    description="Read a cart's current lines and subtotal.",
    parameters={"cart_id": {"type": "string"}},
    required=("cart_id",),
    required_scopes=frozenset({SCOPE_CART_WRITE}),
)

CHECKOUT_CART = ToolSpec(
    name="checkout_cart",
    kind=ToolKind.REQUEST,
    endpoint="checkout_cart",
    description=(
        "Turn a cart into a pending order. This does NOT take payment and does not "
        "commit the buyer to anything; it fixes the lines and totals so they can be "
        "authorized."
    ),
    parameters={
        "cart_id": {"type": "string"},
        "idempotency_key": {
            "type": "string",
            "minLength": 8,
            "description": "Stable key so a retry produces the same order, not a second one.",
        },
    },
    required=("cart_id", "idempotency_key"),
    required_scopes=frozenset({SCOPE_CART_WRITE}),
)

REQUEST_AUTHORIZATION = ToolSpec(
    name="request_authorization",
    kind=ToolKind.REQUEST,
    endpoint="request_authorization",
    description=(
        "Ask the Control Plane for permission to charge an order. This REQUESTS "
        "permission and never grants it: the answer may be approved, pending human "
        "approval, or denied, and the agent cannot influence which. The agent has no "
        "tool that spends an approved authorization."
    ),
    parameters={
        "order_id": {"type": "string", "description": "The pending order to authorize."},
        "amount_paise": {
            "type": "integer",
            "minimum": 1,
            "description": "Integer paise. Must equal the order's final amount.",
        },
    },
    required=("order_id", "amount_paise"),
    required_scopes=frozenset({SCOPE_AUTHORIZATION_REQUEST}),
)

CHECK_AUTHORIZATION = ToolSpec(
    name="check_authorization",
    kind=ToolKind.READ,
    endpoint="get_authorization",
    description=(
        "Read the status of an authorization the agent requested: approved, pending, "
        "denied, consumed, expired or revoked."
    ),
    parameters={"authorization_id": {"type": "string"}},
    required=("authorization_id",),
    required_scopes=frozenset({SCOPE_AUTHORIZATION_READ}),
)


#: The complete tool set. Nothing outside this tuple is callable.
TOOL_SPECS: tuple[ToolSpec, ...] = (
    SEARCH_PRODUCTS,
    GET_PRODUCT,
    CREATE_CART,
    ADD_TO_CART,
    VIEW_CART,
    CHECKOUT_CART,
    REQUEST_AUTHORIZATION,
    CHECK_AUTHORIZATION,
)

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOL_SPECS}


def assert_no_forbidden_tools(names: object = None) -> None:
    """Fail loudly if a money-moving tool has appeared in the set.

    Called by the registry at construction. The check is cheap and the failure
    mode it guards against - a tool added during a busy week, reviewed by
    nobody who remembered the boundary - is the realistic one.
    """
    candidate = set(names) if names is not None else set(TOOL_SPECS_BY_NAME)
    offending = sorted(candidate & set(FORBIDDEN_TOOL_NAMES))
    if offending:
        raise RuntimeError(
            "AI Runtime tool set contains money-moving tools: " + ", ".join(offending)
        )


def tool_schemas() -> list[dict[str, Any]]:
    """Schemas for a tool-calling model, in declaration order."""
    return [spec.to_schema() for spec in TOOL_SPECS]


__all__ = [
    "ADD_TO_CART",
    "CHECKOUT_CART",
    "CHECK_AUTHORIZATION",
    "CREATE_CART",
    "GET_PRODUCT",
    "REQUEST_AUTHORIZATION",
    "SEARCH_PRODUCTS",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "VIEW_CART",
    "ToolKind",
    "ToolSpec",
    "assert_no_forbidden_tools",
    "tool_schemas",
]
