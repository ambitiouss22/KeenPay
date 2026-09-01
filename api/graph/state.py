"""KeenPayState and offer models — see docs/ARCHITECTURE.md."""

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class KeenPayState(TypedDict):
    messages: Annotated[list, add_messages]
    session_id: str
    user_id: str | None
    merchant_id: str
    parsed_intent: dict | None
    search_results: list[dict]
    selected_line_items: list[dict]
    proposed_offer: dict | None
    approved_offer: dict | None
    negotiation_round: int
    guardrail_decision: Literal["APPROVED", "REJECTED", "ESCALATED"] | None
    guardrail_decision_id: str | None
    guardrail_detail: dict | None
    rejection_reasons: list[str]
    user_confirmed_payment: bool
    user_confirmed_at: str | None
    final_amount_paise: int | None
    inventory_reserved: bool
    razorpay_payment_link_id: str | None
    razorpay_payment_link_url: str | None
    order_id: str | None
    anomaly_flags: list[str]
    security_block: bool
    error: dict | None
