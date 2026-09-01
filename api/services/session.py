"""Checkout session orchestrator — graph pipeline without LLM payment tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from config.policy import load_merchant_policy
from core.exceptions import KeenPayError
from policy.engine import PolicyEngine
from policy.models import LineItem, ProposedOffer
from repositories.orders import OrderRepository
from repositories.products import ProductRepository
from repositories.sessions import SessionRepository
from services.audit import AuditService
from services.catalog import CatalogService
from services.razorpay import RazorpayService
from services.trace import TraceService
from utils.money import compute_discount_amount_paise
from utils.money import format_inr as fmt_inr


class SessionService:
    def __init__(self) -> None:
        self._sessions = SessionRepository()
        self._products = ProductRepository()
        self._orders = OrderRepository()
        self._catalog = CatalogService(self._products)
        self._audit = AuditService()
        self._trace = TraceService()
        self._policy = PolicyEngine()
        self._razorpay = RazorpayService()

    async def create_session(
        self, *, merchant_id: str, user_id: str | None, metadata: dict | None
    ) -> dict:
        return await self._sessions.create(
            merchant_id=merchant_id, user_id=user_id, metadata=metadata
        )

    async def get_session(self, session_id: str) -> dict | None:
        return await self._sessions.get(session_id)

    async def process_message(
        self, *, session_id: str, text: str, merchant_id: str
    ) -> dict[str, Any]:
        session = await self._sessions.get(session_id)
        if not session:
            raise KeenPayError("SESSION_NOT_FOUND", "Session not found")

        await self._trace.publish(
            session_id,
            "graph.node.enter",
            node_name="parse_intent",
            payload={"text_len": len(text)},
        )

        qty, sku_hint = self._parse_intent(text)
        products, _ = await self._catalog.search(
            merchant_id=merchant_id, q=sku_hint or "hoodie", limit=5
        )
        if not products:
            return self._assistant_response(
                session_id,
                "I couldn't find matching products. Try describing color or SKU.",
            )

        product = products[0]
        discount_pct = self._suggest_discount(text, session.get("negotiation_round", 0))
        offer = self._build_offer(
            product, qty=qty, discount_pct=discount_pct, version=session.get("offer_version", 0) + 1
        )

        stock = await self._products.stock_map(
            merchant_id=merchant_id, skus=[p["sku"] for p in products]
        )
        decision = self._policy.evaluate(
            offer=offer,
            merchant_id=merchant_id,
            negotiation_round=session.get("negotiation_round", 0),
            user_text=text,
            stock_available=stock,
        )

        await self._audit.log_guardrail(
            session_id=session_id,
            merchant_id=merchant_id,
            decision_id=decision.decision_id,
            offer_version=offer.version,
            input_snapshot={"proposed_offer": offer.model_dump()},
            output_snapshot=decision.model_dump(),
        )
        await self._trace.publish(
            session_id,
            "guardrail.decision",
            payload={"outcome": decision.outcome, "decision_id": decision.decision_id},
        )

        await self._sessions.update(
            session_id,
            proposed_offer=offer.model_dump(),
            approved_offer=decision.approved_offer.model_dump()
            if decision.approved_offer
            else None,
            guardrail_decision=decision.outcome,
            guardrail_decision_id=decision.decision_id,
            guardrail_detail=decision.model_dump(),
            rejection_reasons=decision.rejection_reasons,
            offer_version=offer.version,
            final_amount_paise=decision.approved_offer.final_amount_paise
            if decision.approved_offer
            else None,
            status="awaiting_confirmation" if decision.outcome == "APPROVED" else "negotiating",
            negotiation_round=session.get("negotiation_round", 0) + 1,
        )

        if decision.outcome == "REJECTED":
            reasons = "; ".join(decision.rejection_reasons) or "Policy limits apply"
            return self._assistant_response(session_id, f"I can't offer that discount. {reasons}")
        if decision.outcome == "ESCALATED":
            return self._assistant_response(
                session_id, "I've escalated this to our team for review."
            )

        approved = decision.approved_offer
        assert approved is not None
        msg = (
            f"I found {product['name']} at {fmt_inr(product['list_price_paise'])} each. "
            f"With {approved.discount_pct:.0f}% off, your total is "
            f"{fmt_inr(approved.final_amount_paise)}. "
            "Reply confirm to pay."
        )
        return self._assistant_response(
            session_id,
            msg,
            structured={
                "type": "offer_summary",
                "approved_offer": approved.model_dump(),
                "awaiting_confirmation": True,
            },
        )

    async def confirm_payment(
        self,
        *,
        session_id: str,
        merchant_id: str,
        user_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        session = await self._sessions.get(session_id)
        if not session:
            raise KeenPayError("SESSION_NOT_FOUND", "Session not found")
        if session.get("guardrail_decision") != "APPROVED":
            raise KeenPayError(
                "GUARDRAIL_NOT_APPROVED", "Offer must pass guardrails before payment"
            )

        approved = session.get("approved_offer")
        if not approved:
            raise KeenPayError("NO_APPROVED_OFFER", "No approved offer on session")

        gate_state = {
            "guardrail_decision": "APPROVED",
            "guardrail_decision_id": session.get("guardrail_decision_id"),
            "user_confirmed_payment": True,
            "final_amount_paise": approved["final_amount_paise"],
            "approved_offer": approved,
            "inventory_reserved": True,
            "security_block": session.get("security_block", False),
        }

        link = await self._razorpay.create_payment_link(
            state=gate_state,
            amount_paise=approved["final_amount_paise"],
            description=f"KeenPay order {session_id[:8]}",
            idempotency_key=idempotency_key,
        )

        order = await self._orders.create_pending(
            session_id=session_id,
            merchant_id=merchant_id,
            user_id=user_id,
            line_items=approved["line_items"],
            subtotal_paise=approved["subtotal_paise"],
            discount_amount_paise=approved["discount_amount_paise"],
            final_amount_paise=approved["final_amount_paise"],
            guardrail_decision_id=session["guardrail_decision_id"],
            offer_version=approved["version"],
            policy_version=load_merchant_policy(merchant_id).policy_version,
            idempotency_key=idempotency_key,
            razorpay_payment_link_id=link["payment_link_id"],
            razorpay_payment_link_url=link["payment_link_url"],
        )

        await self._audit.log_payment_link(
            session_id=session_id,
            order_id=order["id"],
            merchant_id=merchant_id,
            decision_id=session["guardrail_decision_id"],
            offer_version=approved["version"],
            output_snapshot={"payment_link_id": link["payment_link_id"]},
        )

        await self._sessions.update(
            session_id,
            user_confirmed_payment=True,
            user_confirmed_at=datetime.now(UTC).isoformat(),
            status="payment_pending",
        )

        return {
            "session_id": session_id,
            "order_id": order["id"],
            "payment_link_id": link["payment_link_id"],
            "payment_link_url": link["payment_link_url"],
            "final_amount_paise": approved["final_amount_paise"],
            "currency": "INR",
            "expires_at": order.get("payment_link_expires_at"),
        }

    def _parse_intent(self, text: str) -> tuple[int, str | None]:
        qty_match = re.search(r"\b(\d+)\b", text)
        qty = int(qty_match.group(1)) if qty_match else 1
        sku_match = re.search(r"\b([A-Z]{2,}-[A-Z0-9-]+)\b", text.upper())
        hint = None
        if sku_match:
            hint = sku_match.group(1)
        elif "hoodie" in text.lower():
            hint = "hoodie"
        elif "tee" in text.lower():
            hint = "tee"
        return min(qty, 10), hint

    def _suggest_discount(self, text: str, round_num: int) -> float:
        if "best price" in text.lower() or "discount" in text.lower():
            return min(10.0 + round_num * 2, 15.0)
        return 5.0

    def _build_offer(
        self, product: dict, *, qty: int, discount_pct: float, version: int
    ) -> ProposedOffer:
        unit = product["list_price_paise"]
        negotiated = round(unit * (1 - discount_pct / 100))
        subtotal = negotiated * qty
        list_subtotal = unit * qty
        return ProposedOffer(
            version=version,
            line_items=[
                LineItem(
                    sku=product["sku"],
                    product_id=product["id"],
                    name=product["name"],
                    quantity=qty,
                    list_unit_price_paise=unit,
                    negotiated_unit_price_paise=negotiated,
                    cost_paise=product["cost_paise"],
                )
            ],
            discount_pct=discount_pct,
            discount_amount_paise=compute_discount_amount_paise(list_subtotal, discount_pct),
            subtotal_paise=subtotal,
            final_amount_paise=subtotal,
            rationale="Agent proposed discount",
        )

    def _assistant_response(
        self,
        session_id: str,
        text: str,
        structured: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "message_id": f"msg_{uuid4().hex[:8]}",
            "role": "assistant",
            "text": text,
            "structured": structured,
            "trace_event_ids": [e["event_id"] for e in self._trace.get_buffer(session_id)[-5:]],
        }
