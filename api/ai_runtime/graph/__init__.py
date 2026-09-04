"""Compiles the agent graph: intent → discover → recommend → assemble → request.

LangGraph owns the topology. The nodes themselves live in
:mod:`ai_runtime.graph.nodes` as plain async functions, so the shape of the
run and the behaviour of each step can be reviewed and tested separately.

There is a fallback runner for the case where LangGraph is not importable.
It is not a design compromise: it executes the same node functions along the
same edges, using the same routing predicates, so behaviour does not change
with its presence. What it buys is that a container missing an optional
dependency degrades to "runs correctly without the graph engine" rather than
to "fails to start", and that the guardrail tests can run anywhere.
"""

from __future__ import annotations

from typing import Any

from ai_runtime.config import AIRuntimeSettings, get_ai_settings
from ai_runtime.graph.nodes import (
    make_assemble,
    make_authorize,
    make_discover,
    make_guardrail,
    parse_intent,
    recommend,
    route_after_assemble,
    route_after_discover,
    route_after_intent,
    route_after_recommend,
)
from ai_runtime.graph.state import AgentRunState
from ai_runtime.tools import ToolRegistry

try:  # pragma: no cover - import-time branch
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without langgraph
    END = START = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False


class SequentialAgentGraph:
    """The same graph, walked by hand.

    Kept deliberately literal: one dict of nodes, one dict of routers, a loop
    with a step ceiling. Reading it is how you check the LangGraph wiring below
    says the same thing.
    """

    def __init__(self, nodes: dict[str, Any], routers: dict[str, Any], entry: str) -> None:
        self._nodes = nodes
        self._routers = routers
        self._entry = entry

    async def ainvoke(self, state: AgentRunState) -> AgentRunState:
        current: str | None = self._entry
        merged: dict[str, Any] = dict(state)
        # The ceiling exists so a routing bug is a bounded failure with a
        # readable state, not a hung request.
        for _ in range(len(self._nodes) * 4):
            if current is None:
                break
            update = await self._nodes[current](merged)  # type: ignore[arg-type]
            merged.update(update or {})
            router = self._routers.get(current)
            current = router(merged) if router else None
        return merged  # type: ignore[return-value]


def build_agent_graph(
    registry: ToolRegistry, *, settings: AIRuntimeSettings | None = None
) -> Any:
    """Wire the nodes into a compiled graph bound to one run's tool registry.

    The registry is closed over rather than carried in the state. State gets
    serialised, logged and checkpointed; a live credentialed HTTP client has no
    business in any of those places.
    """
    settings = settings or get_ai_settings()
    nodes = {
        "parse_intent": parse_intent,
        "discover": make_discover(registry),
        "recommend": recommend,
        "assemble": make_assemble(registry),
        "authorize": make_authorize(registry),
        "guardrail": make_guardrail(registry),
    }
    routers = {
        "parse_intent": route_after_intent,
        "discover": route_after_discover,
        "recommend": route_after_recommend,
        "assemble": route_after_assemble,
        "authorize": lambda _state: "guardrail",
        "guardrail": lambda _state: None,
    }

    if not LANGGRAPH_AVAILABLE:  # pragma: no cover - exercised only without langgraph
        return SequentialAgentGraph(nodes, routers, entry="parse_intent")

    builder = StateGraph(AgentRunState)
    for name, fn in nodes.items():
        builder.add_node(name, fn)

    builder.add_edge(START, "parse_intent")
    builder.add_conditional_edges(
        "parse_intent", route_after_intent, {"discover": "discover", "guardrail": "guardrail"}
    )
    builder.add_conditional_edges(
        "discover", route_after_discover, {"recommend": "recommend", "guardrail": "guardrail"}
    )
    builder.add_conditional_edges(
        "recommend", route_after_recommend, {"assemble": "assemble", "guardrail": "guardrail"}
    )
    builder.add_conditional_edges(
        "assemble", route_after_assemble, {"authorize": "authorize", "guardrail": "guardrail"}
    )
    builder.add_edge("authorize", "guardrail")
    builder.add_edge("guardrail", END)

    # No checkpointer. A checkpointer needs a store, and the only store
    # available would be the Control Plane's Postgres - the exact dependency
    # this service is defined by not having. Runs are single-shot and stateless.
    return builder.compile()


__all__ = ["LANGGRAPH_AVAILABLE", "SequentialAgentGraph", "build_agent_graph"]
