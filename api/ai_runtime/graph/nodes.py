"""The nodes of the agent graph, as plain async functions.

Each node takes the run state and returns only the keys it changed. They are
written to be callable directly, without LangGraph, which is what makes the
guarantees testable: "this run never requested a capture" is asserted against
the node that would have had to do it, not against a mocked graph.

The default planner is deterministic - rules over the buyer's message and the
catalogue's real prices, no model call. Two reasons, and neither is a
placeholder for "add the LLM later":

*Reproducibility.* The same request produces the same plan, so a failing case
is a failing case tomorrow, and CI does not depend on a model provider being
up or on a sampling temperature.

*The interesting part is not the reasoning.* What this service has to prove is
that a plan, however it was produced, can only reach the Control Plane through
allowlisted, non-money-moving tools. A deterministic planner exercises that
path completely. Swapping in a model changes which products get proposed; it
changes nothing about what the runtime is able to do.

:mod:`ai_runtime.agents` is where a model-backed planner plugs in when one is
configured, behind the same interface.
"""

from __future__ import annotations

import re
from typing import Any

from ai_runtime.graph.state import AgentIntent, AgentRunState
from ai_runtime.prompts import REFUSE_DIRECT_PAYMENT
from ai_runtime.tools import ToolRegistry, ToolResult

#: Phrases that ask the agent to do something it must not do. Matching one does
#: not merely fail later - the run refuses up front and says why, which is a
#: better answer than a tool error the buyer cannot interpret.
_REFUSAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b(charge|capture|settle|debit)\b.{0,30}\b(card|account|me|my)\b",
        REFUSE_DIRECT_PAYMENT,
    ),
    (r"\bpay\s+(for\s+)?(it|this|that|them)\s+(now|directly|yourself)\b", REFUSE_DIRECT_PAYMENT),
    (r"\btake\s+(the\s+)?payment\b", REFUSE_DIRECT_PAYMENT),
    (
        r"\brefund\b",
        "I can't issue refunds. A refund goes through KeenPay's own refund review, "
        "and a person has to approve it.",
    ),
    (
        r"\b(approve|authorise|authorize)\s+(it|this|the\s+\w+)\s*(yourself|for\s+me)\b",
        "I can request an authorization, but I can't approve one - that separation is "
        "deliberate. A person with approval rights has to do it.",
    ),
    (
        r"\b(give|get)\s+me\s+a\s+discount\b|\bdiscount\s+(it|this|the\s+price)\b",
        "Prices come from the merchant's catalogue and I can't change them. I can look "
        "for a cheaper product that does the same job.",
    ),
    (
        r"\b(skip|bypass|ignore|override)\b.{0,20}"
        r"\b(authorization|authorisation|approval|policy|limit)\b",
        "I can't bypass the authorization step. Every purchase goes through it.",
    ),
)

_PURCHASE_HINTS = (
    "buy",
    "purchase",
    "order",
    "checkout",
    "check out",
    "get me",
    "i want",
    "i need",
    "add to cart",
)

_STATUS_HINTS = ("status", "did it go through", "was it approved", "is it approved")

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "buy",
        "can",
        "cheap",
        "cheapest",
        "find",
        "for",
        "get",
        "give",
        "i",
        "in",
        "into",
        "is",
        "me",
        "my",
        "need",
        "of",
        "one",
        "order",
        "please",
        "purchase",
        "rs",
        "some",
        "that",
        "the",
        "them",
        "to",
        "two",
        "under",
        "want",
        "with",
        "would",
        "you",
    }
)


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def parse_budget_paise(message: str) -> int | None:
    """Read a budget out of the message, in paise, or ``None``.

    Accepts ``under 500``, ``below ₹1,200``, ``budget of Rs 900``, ``max 250``.
    Rupees are converted with integer arithmetic - ``499.50`` becomes 49950
    paise, never 49949.999.
    """
    pattern = (
        r"(?:under|below|less\s+than|budget\s+of|max(?:imum)?\s+of|max)\s*"
        r"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    )
    match = re.search(pattern, message, flags=re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    if "." in raw:
        whole, frac = raw.split(".", 1)
        return int(whole) * 100 + int(frac.ljust(2, "0")[:2])
    return int(raw) * 100


_BUDGET_PHRASE = re.compile(
    r"(?:under|below|less\s+than|budget\s+of|max(?:imum)?\s+of|max)\s*"
    r"(?:₹|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?",
    flags=re.IGNORECASE,
)


def parse_quantity(message: str) -> int:
    """How many units the buyer wants.

    The budget phrase is stripped first. Without that, "green tea under 500"
    reads 500 as a quantity, and the agent proposes five hundred packets of tea
    to someone who was telling it they had five hundred rupees.
    """
    message = _BUDGET_PHRASE.sub(" ", message)
    match = re.search(r"\b([0-9]{1,3})\s*(?:x|pcs?|pieces?|units?)?\b\s+\w", message)
    if match:
        value = int(match.group(1))
        if 1 <= value <= 1000:
            return value
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", message, flags=re.IGNORECASE):
            return value
    return 1


def parse_search_terms(message: str) -> str:
    """Strip the instruction words so what is left is the thing being shopped."""
    cleaned = re.sub(r"[^\w\s]", " ", message.lower())
    cleaned = re.sub(
        r"\b(under|below|less than|budget of|max|maximum)\b\s*[0-9,.]*", " ", cleaned
    )
    words = [w for w in cleaned.split() if w and w not in _STOPWORDS and not w.isdigit()]
    return " ".join(words[:6])


async def parse_intent(state: AgentRunState) -> dict[str, Any]:
    """Turn the buyer's message into a goal, a query, a quantity and a budget."""
    message = (state.get("message") or "").strip()

    for pattern, refusal in _REFUSAL_PATTERNS:
        if re.search(pattern, message, flags=re.IGNORECASE):
            return {
                "intent": AgentIntent(
                    goal="unsupported",
                    query=parse_search_terms(message),
                    quantity=1,
                    budget_paise=None,
                    refusal_reason=refusal,
                ),
                "stage": "refused",
                "notes": [*state.get("notes", []), "refused: request outside agent authority"],
            }

    lowered = message.lower()
    if any(hint in lowered for hint in _STATUS_HINTS):
        goal = "status"
    elif any(hint in lowered for hint in _PURCHASE_HINTS):
        goal = "purchase"
    else:
        goal = "browse"

    intent = AgentIntent(
        goal=goal,
        query=parse_search_terms(message),
        quantity=parse_quantity(message),
        budget_paise=parse_budget_paise(message),
        refusal_reason=None,
    )
    return {
        "intent": intent,
        "stage": "discover",
        "notes": [*state.get("notes", []), f"intent: {goal}"],
    }


def _fail(state: AgentRunState, message: str, result: ToolResult | None = None) -> dict[str, Any]:
    detail = f"{message}: {result.error}" if result and result.error else message
    return {
        "stage": "failed",
        "errors": [*state.get("errors", []), detail],
    }


def make_discover(registry: ToolRegistry):
    """Search the catalogue. Read-only by construction."""

    async def discover(state: AgentRunState) -> dict[str, Any]:
        intent = state.get("intent", {})
        result = await registry.call(
            "search_products",
            {"query": intent.get("query") or "", "limit": 25},
        )
        if not result.ok:
            return _fail(state, "catalogue search failed", result)

        body = result.data or {}
        items = body.get("items", []) if isinstance(body, dict) else []
        return {
            "candidates": items,
            "stage": "recommend",
            "notes": [*state.get("notes", []), f"catalogue returned {len(items)} product(s)"],
        }

    return discover


async def recommend(state: AgentRunState) -> dict[str, Any]:
    """Rank the candidates and pick what to propose.

    Budget is applied per unit and against the line total, because a buyer who
    says "two, under ₹500" almost always means ₹500 for the pair. Guessing the
    other way silently doubles their spend, which is the worse mistake of the
    two to make on their behalf.
    """
    intent = state.get("intent", {})
    quantity = max(1, int(intent.get("quantity") or 1))
    budget = intent.get("budget_paise")
    limit = max(1, int(state.get("max_recommendations") or 5))

    affordable: list[dict[str, Any]] = []
    for item in state.get("candidates", []):
        if not isinstance(item, dict) or not item.get("sku"):
            continue
        if item.get("active") is False:
            continue
        price = item.get("list_price_paise")
        if not isinstance(price, int):
            continue
        if item.get("quantity_available", quantity) < quantity:
            continue
        if budget is not None and price * quantity > budget:
            continue
        affordable.append(item)

    affordable.sort(key=lambda i: (i.get("list_price_paise", 0), i.get("sku", "")))
    chosen = affordable[:limit]

    if not chosen:
        note = (
            "nothing in the catalogue matches within budget"
            if budget is not None
            else "nothing in the catalogue matches"
        )
        return {
            "recommendations": [],
            "stage": "report",
            "notes": [*state.get("notes", []), note],
        }

    recommendations = [
        {
            "sku": item["sku"],
            "name": item.get("name", item["sku"]),
            "unit_price_paise": item["list_price_paise"],
            "quantity": quantity,
            "line_total_paise": item["list_price_paise"] * quantity,
        }
        for item in chosen
    ]

    next_stage = "assemble" if intent.get("goal") == "purchase" else "report"
    return {
        "recommendations": recommendations,
        "stage": next_stage,
        "notes": [
            *state.get("notes", []),
            f"recommending {len(recommendations)} product(s)",
        ],
    }


def make_assemble(registry: ToolRegistry):
    """Open a cart, add the top recommendation, and create a pending order.

    Only the first recommendation is bought. The rest are shown to the buyer as
    alternatives. An agent that added every candidate it liked would be
    spending someone else's money on its own taste.
    """

    async def assemble(state: AgentRunState) -> dict[str, Any]:
        recommendations = state.get("recommendations") or []
        if not recommendations:
            return {"stage": "report"}

        pick = recommendations[0]

        cart = await registry.call("create_cart", {})
        if not cart.ok:
            return _fail(state, "could not open a cart", cart)
        cart_id = (cart.data or {}).get("id")
        if not cart_id:
            return _fail(state, "cart response carried no id")

        added = await registry.call(
            "add_to_cart",
            {"cart_id": cart_id, "sku": pick["sku"], "quantity": pick["quantity"]},
        )
        if not added.ok:
            return {**_fail(state, "could not add the item to the cart", added), "cart_id": cart_id}

        checked_out = await registry.call(
            "checkout_cart",
            {"cart_id": cart_id, "idempotency_key": state["idempotency_key"]},
        )
        if not checked_out.ok:
            return {
                **_fail(state, "could not create the order", checked_out),
                "cart_id": cart_id,
            }

        order = checked_out.data or {}
        total = order.get("final_amount_paise")
        if not isinstance(total, int) or total <= 0:
            return {
                **_fail(state, "order response carried no usable total"),
                "cart_id": cart_id,
                "order_id": order.get("id"),
            }

        return {
            "cart_id": cart_id,
            "order_id": order.get("id"),
            "order_total_paise": total,
            "stage": "authorize",
            "notes": [
                *state.get("notes", []),
                f"pending order created for {_rupees(total)} - nothing charged",
            ],
        }

    return assemble


def make_authorize(registry: ToolRegistry):
    """Ask for permission. This is the furthest the agent can go.

    The amount sent is the order's own total as the Control Plane computed it,
    read back from the checkout response - not a number this service worked
    out. The Control Plane re-derives it again on arrival; sending anything
    else would simply be rejected, and sending the total it just told us is the
    honest expression of "authorize what this order costs".
    """

    async def authorize(state: AgentRunState) -> dict[str, Any]:
        order_id = state.get("order_id")
        total = state.get("order_total_paise")
        if not order_id or not isinstance(total, int):
            return {"stage": "report"}

        ceiling = int(state.get("max_request_amount_paise") or 0)
        if ceiling and total > ceiling:
            return {
                "stage": "report",
                "notes": [
                    *state.get("notes", []),
                    "order exceeds this runtime's per-run request ceiling; not requested",
                ],
                "errors": [
                    *state.get("errors", []),
                    f"order total {_rupees(total)} exceeds the agent's request ceiling "
                    f"of {_rupees(ceiling)}",
                ],
            }

        result = await registry.call(
            "request_authorization", {"order_id": order_id, "amount_paise": total}
        )
        if not result.ok:
            return {**_fail(state, "authorization request failed", result), "stage": "report"}

        record = result.data or {}
        return {
            "authorization_id": record.get("id"),
            "authorization_status": record.get("status"),
            "authorization_reasons": list(record.get("reasons") or []),
            "stage": "report",
            "notes": [
                *state.get("notes", []),
                f"authorization requested: {record.get('status')}",
            ],
        }

    return authorize


def make_guardrail(registry: ToolRegistry):
    """Audit what the run actually did, after it did it.

    A second check, downstream of the ones that already made a violation
    impossible. Its value is that it reads evidence - the recorded invocations -
    rather than intent, so it would catch a future edit that reached the
    Control Plane by some path the allowlist did not cover. Cheap, and the run
    report carries its verdict, which is what an auditor asks for.
    """

    async def guardrail(state: AgentRunState) -> dict[str, Any]:
        from ai_runtime.isolation import FORBIDDEN_TOOL_NAMES
        from ai_runtime.tools.tool_defs import TOOL_SPECS_BY_NAME

        violations: list[str] = []
        for invocation in registry.invocations:
            name = invocation["tool"]
            if name in FORBIDDEN_TOOL_NAMES:
                violations.append(f"forbidden tool invoked: {name}")
            elif name not in TOOL_SPECS_BY_NAME:
                violations.append(f"unregistered tool invoked: {name}")

        reply = _compose_reply(state)
        return {
            "guardrail_ok": not violations,
            "guardrail_violations": violations,
            "tool_calls": list(registry.invocations),
            "reply": reply,
            "stage": "report",
        }

    return guardrail


def _compose_reply(state: AgentRunState) -> str:
    """The buyer-facing summary.

    Written here rather than by a model so that the one sentence that must
    never be wrong - whether money moved - is generated by code that knows the
    answer, not by something predicting a plausible next token.
    """
    intent = state.get("intent", {})

    if intent.get("goal") == "unsupported":
        return intent.get("refusal_reason") or REFUSE_DIRECT_PAYMENT

    errors = state.get("errors") or []
    recommendations = state.get("recommendations") or []

    if not recommendations:
        base = "I couldn't find anything in the catalogue that matches"
        if intent.get("budget_paise"):
            base += f" within {_rupees(int(intent['budget_paise']))}"
        base += "."
        if errors:
            base += f" ({errors[0]})"
        return base

    lines = [
        f"- {r['name']} ({r['sku']}) x{r['quantity']} — {_rupees(r['line_total_paise'])}"
        for r in recommendations
    ]
    parts = ["Here's what I'd suggest:", *lines]

    order_id = state.get("order_id")
    total = state.get("order_total_paise")
    if order_id and isinstance(total, int):
        parts.append(
            f"\nI've put the first item into a pending order ({order_id}) "
            f"totalling {_rupees(total)}. Nothing has been charged."
        )

    status = state.get("authorization_status")
    auth_id = state.get("authorization_id")
    if status == "approved":
        parts.append(
            f"The authorization ({auth_id}) is approved. KeenPay will take the payment; "
            "I can't and don't."
        )
    elif status == "pending":
        parts.append(
            f"The authorization ({auth_id}) is waiting on human approval. "
            "Nothing is charged until someone approves it."
        )
    elif status == "denied":
        reasons = "; ".join(state.get("authorization_reasons") or []) or "no reason given"
        parts.append(f"The authorization was denied ({reasons}). Nothing has been charged.")

    if errors:
        parts.append(f"\nOne thing didn't work: {errors[0]}")

    return "\n".join(parts)


def route_after_intent(state: AgentRunState) -> str:
    """A refusal skips straight to the audit-and-reply node.

    It does not search, does not open a cart, and makes no Control Plane call
    at all - a request the agent must refuse should leave no trace on the
    buyer's account.
    """
    return "guardrail" if state.get("stage") == "refused" else "discover"


def route_after_discover(state: AgentRunState) -> str:
    return "guardrail" if state.get("stage") == "failed" else "recommend"


def route_after_recommend(state: AgentRunState) -> str:
    return "assemble" if state.get("stage") == "assemble" else "guardrail"


def route_after_assemble(state: AgentRunState) -> str:
    return "authorize" if state.get("stage") == "authorize" else "guardrail"


__all__ = [
    "make_assemble",
    "make_authorize",
    "make_discover",
    "make_guardrail",
    "parse_budget_paise",
    "parse_intent",
    "parse_quantity",
    "parse_search_terms",
    "recommend",
    "route_after_assemble",
    "route_after_discover",
    "route_after_intent",
    "route_after_recommend",
]
