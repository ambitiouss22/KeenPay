"""Wire contracts for the AI Runtime's own API.

Two rules, both borrowed from the Control Plane because the reasoning is the
same on this side of the boundary.

**Money is integer paise.** Never a float, never a formatted string in a field
something computes with.

**Identity is never a body field.** There is no ``merchant_id`` and no
``user_id`` on any request here. Both come from the agent credential, which the
Control Plane verifies. A body that could name its own merchant would let one
caller shop another merchant's catalogue through this service.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    """One buyer intent, in natural language."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    merchant_name: str | None = Field(default=None, max_length=200)
    #: Supplied when the caller wants a retry to be recognised as the same run.
    #: Omitted, one is derived from the run id, which is unique per call.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class ToolCallOut(BaseModel):
    id: str
    tool: str
    kind: Literal["read", "request"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    error: str | None = None
    status_code: int | None = None


class ControlPlaneCallOut(BaseModel):
    endpoint: str
    method: str
    path: str
    status_code: int | None = None
    error: str | None = None


class RecommendationOut(BaseModel):
    sku: str
    name: str
    unit_price_paise: int
    quantity: int
    line_total_paise: int


class AgentRunResponse(BaseModel):
    """The run report.

    ``money_moved`` is always ``false`` and is present anyway. A caller
    integrating against this service should be able to read the guarantee off
    the response rather than having to trust a paragraph of documentation, and
    a contract test can assert it.
    """

    run_id: str
    stage: str
    reply: str
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    cart_id: str | None = None
    order_id: str | None = None
    order_total_paise: int | None = None
    authorization_id: str | None = None
    authorization_status: str | None = None
    authorization_reasons: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    control_plane_calls: list[ControlPlaneCallOut] = Field(default_factory=list)
    guardrail_ok: bool = True
    guardrail_violations: list[str] = Field(default_factory=list)
    money_moved: Literal[False] = False
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ToolDescriptionOut(BaseModel):
    name: str
    kind: Literal["read", "request"]
    description: str
    endpoint: str
    scopes: list[str] = Field(default_factory=list)


class ToolListResponse(BaseModel):
    """The agent's complete power, readable without running anything."""

    tools: list[ToolDescriptionOut]
    forbidden: list[str] = Field(default_factory=list)
    allowlisted_endpoints: list[str] = Field(default_factory=list)


class RuntimeHealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    isolated: bool
    graph_engine: str
    violations: list[str] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    """Same shape the Control Plane uses, so a client parses one contract."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentRunRequest",
    "AgentRunResponse",
    "ControlPlaneCallOut",
    "ErrorEnvelope",
    "RecommendationOut",
    "RuntimeHealthOut",
    "ToolCallOut",
    "ToolDescriptionOut",
    "ToolListResponse",
]
