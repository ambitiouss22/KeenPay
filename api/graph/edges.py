"""Conditional edge routing for KeenPayStateGraph."""

from graph.state import KeenPayState


def after_guardrail(state: KeenPayState) -> str:
    decision = state.get("guardrail_decision")
    if decision == "APPROVED":
        return "approved"
    if decision == "ESCALATED":
        return "escalated"
    return "rejected"


def after_rejection(state: KeenPayState) -> str:
    if state.get("negotiation_round", 0) >= 5:
        return "max_rounds"
    return "retry_negotiate"
