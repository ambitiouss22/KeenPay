#!/usr/bin/env python3
"""Generate KeenPay Production Architecture & Systems Design PDF."""

from pathlib import Path
from fpdf import FPDF

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "KeenPay_Architecture_Workflow.pdf"

NAVY = (15, 23, 42)
BLUE = (37, 99, 235)
LIGHT_BLUE = (219, 234, 254)
GREEN = (22, 163, 74)
LIGHT_GREEN = (220, 252, 231)
ORANGE = (234, 88, 12)
LIGHT_ORANGE = (255, 237, 213)
RED = (220, 38, 38)
LIGHT_RED = (254, 226, 226)
GRAY = (100, 116, 139)
LIGHT_GRAY = (241, 245, 249)
WHITE = (255, 255, 255)
PURPLE = (124, 58, 237)
AMBER = (202, 138, 4)


class KeenPayPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, "KeenPay - Production Architecture & Systems Design", align="C")
        self.ln(4)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, f"Page {self.page_no() - 1}", align="C")

    def section_title(self, title: str):
        self.ln(3)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*NAVY)
        self.cell(0, 9, title)
        self.ln(7)

    def subsection_title(self, title: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BLUE)
        self.cell(0, 7, title)
        self.ln(5)

    def body_text(self, text: str):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 4.5, text)
        self.ln(2)

    def bullet(self, text: str, indent: int = 20):
        self.set_x(indent)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 4.5, f"- {text}")

    def draw_layer_box(self, x, y, w, h, title, lines, fill, border, title_color=NAVY):
        self.set_fill_color(*fill)
        self.set_draw_color(*border)
        self.set_line_width(0.4)
        self.rect(x, y, w, h, style="DF")
        self.set_xy(x + 3, y + 3)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*title_color)
        self.cell(w - 6, 4, title)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(30, 41, 59)
        ly = y + 8
        for line in lines:
            self.set_xy(x + 4, ly)
            self.cell(w - 8, 3.5, line)
            ly += 3.5

    def draw_arrow_down(self, cx, y1, y2):
        self.set_draw_color(*GRAY)
        self.set_line_width(0.3)
        self.line(cx, y1, cx, y2)
        self.line(cx, y2, cx - 2, y2 - 3)
        self.line(cx, y2, cx + 2, y2 - 3)


def build_cover(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")
    pdf.set_y(55)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 12, "KeenPay", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*LIGHT_BLUE)
    pdf.cell(0, 10, "Production Architecture & Systems Design", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(203, 213, 225)
    pdf.cell(0, 6, "Document Status: Approved for Implementation", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Author: Lead Systems Architect", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(16)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 6, "Grow  |  Sell  |  Protect", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "FastAPI  |  LangGraph  |  PostgreSQL  |  Redis  |  Next.js  |  Razorpay", align="C")
    pdf.set_y(255)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 6, "Version 1.0.0  |  August 2026", align="C")


def build_philosophy(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.section_title("1. Engineering Philosophy")

    pdf.body_text(
        "As a rule, AI models are non-deterministic and therefore implicitly untrusted with "
        "financial execution. KeenPay is designed with a strict Air-Gap philosophy: the AI engine "
        "handles the Grow (upselling, negotiation, discovery) and Sell (cart assembly) logic, but "
        "all financial API calls to Razorpay are firmly locked behind a deterministic Protect layer."
    )
    pdf.body_text(
        "We prioritize a pragmatic, monolithic-first API using FastAPI, PostgreSQL, and Redis. "
        "It is simple enough to deploy in minutes, but robust enough to handle enterprise concurrency "
        "and strict audit compliance."
    )

    pdf.subsection_title("Air-Gap Model: Grow / Sell / Protect")
    col_w = 58
    y = pdf.get_y()
    pdf.draw_layer_box(15, y, col_w, 28, "GROW (AI / LangGraph)", [
        "Intent parsing",
        "Catalog RAG search",
        "Upsell & negotiation",
    ], LIGHT_BLUE, BLUE)
    pdf.draw_layer_box(15 + col_w + 4, y, col_w, 28, "SELL (Cart Assembly)", [
        "Cart Assembler node",
        "Price Calculator (int paise)",
        "Checkout intent emitter",
    ], LIGHT_ORANGE, ORANGE)
    pdf.draw_layer_box(15 + 2 * (col_w + 4), y, col_w, 28, "PROTECT (Deterministic)", [
        "Policy + Risk checks",
        "Authorization gate",
        "Razorpay execution",
    ], LIGHT_GREEN, GREEN)
    pdf.set_y(y + 34)

    pdf.subsection_title("Trust Boundaries")
    pdf.bullet("UNTRUSTED: User input, LLM output, inbound webhooks (validated on receipt)")
    pdf.bullet("SEMI-TRUSTED: LangGraph orchestration (proposes actions, never pays)")
    pdf.bullet("TRUSTED: Guardrail Engine, integer math, Razorpay client (gated side effects)")


def build_architecture_diagram(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.section_title("2. System Architecture (Grow & Sell)")

    pdf.body_text(
        "This architecture separates conversational intelligence from the financial control plane. "
        "The client tier uses HTTPS/WebSockets for chat and SSE for live audit traces."
    )

    x, w = 15, 180
    y = pdf.get_y() + 2

    # Client tier
    pdf.draw_layer_box(x, y, w, 22, "CLIENT TIER (Next.js)", [
        "Chat UI: Natural Language  |  Audit UI: Live Graph & Guardrail Traces",
    ], (224, 231, 255), BLUE)
    pdf.draw_arrow_down(x + w / 2, y + 22, y + 28)
    y += 28

    # API Gateway
    pdf.draw_layer_box(x, y, w, 20, "KEENPAY API GATEWAY (FastAPI)", [
        "AuthN/AuthZ  |  Rate Limiting  |  Pydantic Validation",
    ], LIGHT_GRAY, GRAY)
    pdf.draw_arrow_down(x + w / 2, y + 20, y + 26)
    y += 26

    # AI Orchestration
    pdf.draw_layer_box(x, y, w, 38, "AI ORCHESTRATION (LangGraph)", [
        "GROW: Intent Parser | Catalog RAG | Upsell/Negotiator Agent",
        "SELL: Cart Assembler | Price Calculator (Strict Integer Math) | Checkout Emitter",
    ], (243, 232, 255), PURPLE)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*RED)
    pdf.set_xy(x + 3, y + 38)
    pdf.cell(w, 4, "PROPOSED ACTION (Untrusted Payload)")
    pdf.draw_arrow_down(x + w / 2, y + 42, y + 48)
    y += 48

    # Protect layer
    pdf.draw_layer_box(x, y, w, 34, 'DETERMINISTIC GUARDRAIL ENGINE ("Protect" Layer)', [
        "Policy Check: Max Discount < 15% | Stock > 0",
        "Risk Check: Velocity Anomaly | Prompt Injection",
        "Authorization Gate: Auto-Approve (Low Risk) | Step-Up / Human (High Risk)",
    ], LIGHT_GREEN, GREEN)
    y += 38

    # Split: approved vs rejected
    half = w / 2 - 2
    pdf.draw_layer_box(x, y, half, 30, "PAYMENT EXECUTION (Razorpay)", [
        "Idempotency Key | Create Payment Link",
        "Emit Event to Audit Ledger",
    ], LIGHT_RED, RED)
    pdf.draw_layer_box(x + half + 4, y, half, 30, "GRACEFUL FALLBACK", [
        "Halt Cart Execution | Safe UI Response",
        "Dispatch Ticket to Merchant",
    ], (254, 243, 199), AMBER)
    pdf.draw_arrow_down(x + w / 4, y + 30, y + 36)
    y += 36

    # Infrastructure
    pdf.draw_layer_box(x, y, w, 24, "STATE & INFRASTRUCTURE (PostgreSQL & Redis)", [
        "Redis: Distributed Locks (Inventory), LangGraph Session Memory",
        "PostgreSQL: RLS Catalog, Immutable Audit Ledger, Transaction Passports",
    ], (254, 226, 226), RED)


def build_workflow_grow(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.section_title("3. Workflow Execution Steps")
    pdf.body_text(
        "The lifecycle of a KeenPay interaction is strictly linear. An AI cannot skip a step."
    )

    pdf.subsection_title("Phase 1: GROW (Discovery & Revenue Optimization)")
    steps_grow = [
        ("User Intent", 'Buyer asks: "I need a mechanical keyboard, but only if I can get a discount on two."'),
        ("Context Retrieval", "LangGraph queries PostgreSQL/pgvector catalog for mechanical keyboards and retrieves buyer LTV."),
        ("Agentic Upsell", 'Agent replies: "I can offer the Keychron K2. If you buy two, I can apply a 10% bundle discount."'),
        ("User Agreement", "User agrees. Cart Assembler constructs JSON: quantity=2, discount=10% (proposed, not final)."),
    ]
    for i, (title, desc) in enumerate(steps_grow, 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*BLUE)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {title}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, desc)
        pdf.ln(2)

    pdf.subsection_title("Phase 2: PROTECT (The Guardrail Interception)")
    pdf.body_text(
        "Before any API call to Razorpay, the payload is intercepted by the Python Guardrail Engine."
    )
    steps_protect = [
        ("Inventory Lock", "Redis attempts to claim stock by 2. If it fails, checkout halts immediately."),
        ("Policy Assertion", "Python asserts proposed_discount <= merchant.max_discount_limit. AI-hallucinated 50% is clamped to 15% or blocked."),
        ("Price Re-computation", "Final total calculated in integer paise by backend, NOT the LLM, to prevent tampering."),
        ("Risk Scoring", "Unusually large transactions trigger SEND TO HUMAN workflow, halting automated checkout."),
    ]
    for i, (title, desc) in enumerate(steps_protect, 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*GREEN)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {title}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, desc)
        pdf.ln(2)


def build_workflow_sell(pdf: KeenPayPDF):
    pdf.subsection_title("Phase 3: SELL (Razorpay Execution & Settlement)")
    steps_sell = [
        ("Idempotent Order Creation", "Backend generates unique cart hash as Idempotency-Key. Calls Razorpay POST /v1/payment_links."),
        ("Link Delivery", "Payment Link pushed via WebSocket to Next.js chat interface."),
        ("Webhook Reconciliation", "User pays. Razorpay fires payment_link.paid. KeenPay verifies HMAC webhook signature."),
        ("Audit Commit", "Final state written to audit_ledger. Transaction Passport links Razorpay Order ID to LLM node and policy version."),
    ]
    for i, (title, desc) in enumerate(steps_sell, 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*ORANGE)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {title}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, desc)
        pdf.ln(2)

    pdf.ln(4)
    pdf.subsection_title("Linear Flow Enforcement")
    flow = "GROW -> PROPOSE -> PROTECT -> [APPROVED] -> SELL -> SETTLE  |  [REJECTED] -> FALLBACK"
    pdf.set_font("Courier", "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.set_x(18)
    pdf.multi_cell(175, 5, flow)


def build_security_matrix(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.section_title("4. Hardcoded Guardrails (Security Matrix)")

    pdf.body_text(
        "To guarantee system integrity, these guardrails are enforced at the API level. "
        "No LLM prompt can override them."
    )

    rows = [
        ("Data Isolation", "Cross-merchant data leaks", "Row-Level Security (RLS) in PostgreSQL. Merchant A cannot query Merchant B catalog or orders."),
        ("Double Spend", "Network timeout causes payment retry", "Strict Idempotency + Never-Retry-Unknown protocol. Razorpay timeout marks state UNKNOWN; background worker polls."),
        ("Margin Erosion", "Prompt injection forces Rs.1 offer", "Absolute Price Floor Policy. Backend recalculates (Base Price * Qty) - Max Allowed Discount."),
        ("Concurrency", "Two agents sell last item", "Redis Distributed Locks claim inventory before Payment Link generation; release on timeout."),
        ("Untraceability", "Cannot debug bad transaction", "Transaction Passport: immutable log linking Razorpay Order ID to LLM prompt and policy version."),
        ("Prompt Injection", "Bypass rules via chat", "Deterministic regex + anomaly scorer; security_block halts money actions."),
        ("LLM Arithmetic", "Wrong discount math", "Price Calculator node uses Decimal integer paise; LLM excluded from totals."),
        ("Webhook Tamper", "Forged payment events", "HMAC-SHA256 signature verify; unique event_id; amount reconciliation."),
    ]

    col_w = [38, 42, 100]
    headers = ["Guardrail Vector", "Threat", "KeenPay Deterministic Solution"]

    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 7)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    for idx, (vector, threat, solution) in enumerate(rows):
        if pdf.get_y() > 250:
            pdf.add_page()
            pdf.set_fill_color(*NAVY)
            pdf.set_text_color(*WHITE)
            pdf.set_font("Helvetica", "B", 7)
            for i, h in enumerate(headers):
                pdf.cell(col_w[i], 7, h, border=1, fill=True)
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)

        fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
        pdf.set_fill_color(*fill)
        pdf.set_text_color(30, 41, 59)
        y0 = pdf.get_y()
        pdf.cell(col_w[0], 5, vector, border=1, fill=True)
        pdf.cell(col_w[1], 5, threat, border=1, fill=True)
        x_sol = pdf.get_x()
        pdf.multi_cell(col_w[2], 4, solution, border=1, fill=True)
        y1 = pdf.get_y()
        if y1 - y0 < 5:
            pdf.set_xy(pdf.l_margin + col_w[0] + col_w[1] + col_w[2], y1)
        else:
            pdf.set_xy(pdf.l_margin, y1)

    pdf.ln(6)
    pdf.subsection_title("Authorization Gate Outcomes")
    outcomes = [
        ("AUTO-APPROVE", "Low risk: discount within policy, stock available, anomaly score < 0.5", GREEN),
        ("CLAMP", "Discount exceeds cap: force to merchant.max_discount_limit", ORANGE),
        ("REJECT", "Margin violation or invalid SKU: halt, explain to user", RED),
        ("STEP-UP / HUMAN", "High risk or max negotiation rounds: escalation ticket to merchant", PURPLE),
    ]
    for label, desc, color in outcomes:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*color)
        pdf.set_x(18)
        pdf.cell(40, 5, label)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.multi_cell(140, 4.5, desc)


def build_transaction_passport(pdf: KeenPayPDF):
    pdf.add_page()
    pdf.section_title("5. Transaction Passport & Audit Ledger")

    pdf.body_text(
        "Every finalized cart generates an immutable Transaction Passport stored in PostgreSQL audit_logs. "
        "This proves exactly which AI node proposed the discount and which policy version authorized it."
    )

    pdf.subsection_title("Passport Fields")
    fields = [
        ("passport_id", "UUID v4 primary reference"),
        ("session_id", "LangGraph thread / negotiation session"),
        ("razorpay_order_id", "Razorpay Payment Link or Order ID"),
        ("offer_version", "Monotonic cart proposal version"),
        ("decision_id", "Guardrail evaluation reference"),
        ("policy_version", "e.g. 2026.08.1"),
        ("grow_node_trace", "JSONB: intent, catalog hits, negotiation rationale"),
        ("protect_node_trace", "JSONB: per-rule pass/fail/clamp results"),
        ("sell_node_trace", "JSONB: idempotency_key, link_id, webhook events"),
        ("final_amount_paise", "Deterministic integer total"),
        ("created_at", "UTC timestamp (append-only)"),
    ]
    for field, desc in fields:
        pdf.set_font("Courier", "B", 8)
        pdf.set_text_color(*BLUE)
        pdf.set_x(18)
        pdf.cell(45, 5, field)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 5, desc, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.subsection_title("Observability Channels")
    pdf.bullet("WebSocket trace:{session_id} - real-time graph node + guardrail events to Audit UI")
    pdf.bullet("SSE /ws/v1/session - Server-Sent Events for live state transitions")
    pdf.bullet("PostgreSQL audit_logs - durable append-only ledger (UPDATE/DELETE blocked by trigger)")
    pdf.bullet("Redis pub/sub - fan-out trace events to connected clients")

    pdf.ln(4)
    pdf.subsection_title("Deployment Stack")
    stack = [
        ("Frontend", "Next.js 14 - Chat UI + Audit UI (split pane)"),
        ("API", "FastAPI - monolithic gateway + LangGraph runtime"),
        ("Orchestration", "LangGraph - KeenPayStateGraph with interrupt before payment"),
        ("Database", "PostgreSQL 15 - RLS, pgvector catalog, audit ledger"),
        ("Cache", "Redis 7 - locks, session memory, rate limits, pub/sub"),
        ("Payments", "Razorpay Payment Links API (test + production modes)"),
    ]
    for component, detail in stack:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*NAVY)
        pdf.set_x(18)
        pdf.cell(35, 5, component)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 5, detail, new_x="LMARGIN", new_y="NEXT")


def main():
    pdf = KeenPayPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)

    build_cover(pdf)
    build_philosophy(pdf)
    build_architecture_diagram(pdf)
    build_workflow_grow(pdf)
    build_workflow_sell(pdf)
    build_security_matrix(pdf)
    build_transaction_passport(pdf)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUTPUT))
    print(f"PDF generated: {OUTPUT}")


if __name__ == "__main__":
    main()
