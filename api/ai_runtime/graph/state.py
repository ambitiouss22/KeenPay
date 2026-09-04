"""The state one agent run carries from node to node.

A plain ``TypedDict`` with ``total=False`` so LangGraph can merge partial
updates: a node returns only the keys it changed and the graph applies them.

Money is integer paise throughout, as it is everywhere else in this system. A
float here would be a rounding error that reaches an authorization request.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

RunStage = Literal[
    "parse_intent",
    "discover",
    "recommend",
    "assemble",
    "authorize",
    "report",
    "refused",
    "failed",
]


class AgentIntent(TypedDict, total=False):
    """What the buyer asked for, once parsed into something actionable."""

    goal: Literal["browse", "purchase", "status", "unsupported"]
    query: str
    quantity: int
    budget_paise: int | None
    #: Set when the buyer asked for something the agent must refuse - paying
    #: directly, a discount, an approval. Carried so the report can explain the
    #: refusal instead of silently doing something else.
    refusal_reason: str | None


class AgentRunState(TypedDict, total=False):
    """Everything a run reads and writes."""

    # --- inputs ---
    run_id: str
    message: str
    merchant_name: str | None
    max_recommendations: int
    max_request_amount_paise: int
    idempotency_key: str

    # --- reasoning ---
    intent: AgentIntent
    stage: RunStage
    notes: list[str]

    # --- discovery ---
    candidates: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]

    # --- assembly (nothing here has moved money) ---
    cart_id: str | None
    order_id: str | None
    order_total_paise: int | None

    # --- the request, and only ever a request ---
    authorization_id: str | None
    authorization_status: str | None
    authorization_reasons: list[str]

    # --- output ---
    reply: str
    errors: list[str]
    tool_calls: list[dict[str, Any]]
    guardrail_ok: bool
    guardrail_violations: list[str]


def new_state(
    *,
    run_id: str,
    message: str,
    idempotency_key: str,
    merchant_name: str | None = None,
    max_recommendations: int = 5,
    max_request_amount_paise: int = 0,
) -> AgentRunState:
    """A run's starting state, with every list present and empty.

    Pre-populating the collections means no node has to write ``state.get(k) or
    []``, and a node that appends to a missing key fails at the test that first
    exercises it rather than in production.
    """
    return AgentRunState(
        run_id=run_id,
        message=message,
        merchant_name=merchant_name,
        max_recommendations=max_recommendations,
        max_request_amount_paise=max_request_amount_paise,
        idempotency_key=idempotency_key,
        intent=AgentIntent(goal="browse", query="", quantity=1, budget_paise=None),
        stage="parse_intent",
        notes=[],
        candidates=[],
        recommendations=[],
        cart_id=None,
        order_id=None,
        order_total_paise=None,
        authorization_id=None,
        authorization_status=None,
        authorization_reasons=[],
        reply="",
        errors=[],
        tool_calls=[],
        guardrail_ok=True,
        guardrail_violations=[],
    )


__all__ = ["AgentIntent", "AgentRunState", "RunStage", "new_state"]
