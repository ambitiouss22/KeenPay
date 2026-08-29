#!/usr/bin/env python3
"""Build AEGISPAY Agentic Commerce Architecture V4 PDF."""

from pathlib import Path
from fpdf import FPDF

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "AEGISPAY_Agentic_Commerce_Architecture_V4.pdf"
OUTPUT_DL = Path.home() / "Downloads" / "AEGISPAY_Agentic_Commerce_Architecture_V4.pdf"

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
TEAL = (13, 148, 136)
LIGHT_TEAL = (204, 251, 241)


class AegisPayPDF(FPDF):
    DOC_TITLE = "AegisPay - Agentic Commerce Architecture V4"

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, self.DOC_TITLE, align="C")
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

    def draw_layer_box(self, x, y, w, h, title, lines, fill, border, title_color=NAVY, font_size=7):
        self.set_fill_color(*fill)
        self.set_draw_color(*border)
        self.set_line_width(0.4)
        self.rect(x, y, w, h, style="DF")
        self.set_xy(x + 3, y + 3)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*title_color)
        self.cell(w - 6, 4, title)
        self.set_font("Helvetica", "", font_size)
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

    def badge(self, label: str, color):
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*color)
        self.cell(28, 4, label)

    def simple_table(self, headers, rows, col_w):
        if self.get_y() > 240:
            self.add_page()
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 7)
        for i, h in enumerate(headers):
            self.cell(col_w[i], 6, h, border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 7)
        for idx, row in enumerate(rows):
            if self.get_y() > 265:
                self.add_page()
                self.set_fill_color(*NAVY)
                self.set_text_color(*WHITE)
                self.set_font("Helvetica", "B", 7)
                for i, h in enumerate(headers):
                    self.cell(col_w[i], 6, h, border=1, fill=True)
                self.ln()
                self.set_font("Helvetica", "", 7)
            fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
            self.set_fill_color(*fill)
            self.set_text_color(30, 41, 59)
            y0 = self.get_y()
            for i, cell in enumerate(row):
                if i == len(row) - 1 and len(cell) > 40:
                    x0 = self.get_x()
                    self.multi_cell(col_w[i], 4, cell, border=1, fill=True)
                    self.set_xy(x0 + col_w[i], y0)
                else:
                    self.cell(col_w[i], 5, cell, border=1, fill=True)
            self.ln(max(5, self.get_y() - y0))


def build_cover(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")
    pdf.set_y(48)
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 14, "AEGISPAY", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*LIGHT_BLUE)
    pdf.cell(0, 10, "Agentic Commerce Architecture V4", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(203, 213, 225)
    pdf.multi_cell(0, 6, (
        "Different protocols can enter AegisPay,\n"
        "but none can bypass the AegisPay Control Plane."
    ), align="C")
    pdf.ln(14)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 6, "Grow  |  Sell  |  Protect  |  Protocol Gateway", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "FastAPI  |  LangGraph  |  PostgreSQL  |  Redis  |  AWS  |  Razorpay", align="C")
    pdf.set_y(255)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 6, "August 2026", align="C")


def build_philosophy(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("1. Engineering Philosophy")

    pdf.body_text(
        "AI models are non-deterministic. They are good at language, negotiation, and routing. "
        "They are not trusted to move money. AegisPay keeps a strict air-gap: agents and external "
        "protocols may propose commerce actions, but only the deterministic Control Plane may "
        "authorize and execute payment."
    )
    pdf.body_text(
        "V4 adds a Protocol Gateway so UCP, ACP, AP2, A2A, MCP, x402, A2UI, and India rails "
        "(NPCI UAP / UPI) can enter through adapters - not through separate payment stacks. "
        "Every adapter normalizes to one AegisPay Intent. One policy engine. One audit trail."
    )

    pdf.subsection_title("Air-Gap Model: Grow / Sell / Protect")
    col_w = 58
    y = pdf.get_y()
    pdf.draw_layer_box(15, y, col_w, 28, "GROW (AI / LangGraph)", [
        "Intent parsing",
        "Catalog search",
        "Upsell & negotiation",
    ], LIGHT_BLUE, BLUE)
    pdf.draw_layer_box(15 + col_w + 4, y, col_w, 28, "SELL (Cart / Checkout)", [
        "Cart assembly",
        "Price in integer paise",
        "Checkout intent emit",
    ], LIGHT_ORANGE, ORANGE)
    pdf.draw_layer_box(15 + 2 * (col_w + 4), y, col_w, 28, "PROTECT (Control Plane)", [
        "Policy + risk",
        "Authorization gate",
        "Payment execution",
    ], LIGHT_GREEN, GREEN)
    pdf.set_y(y + 34)

    pdf.subsection_title("Trust Boundaries")
    pdf.bullet("UNTRUSTED: protocol payloads, user input, LLM output, inbound webhooks")
    pdf.bullet("SEMI-TRUSTED: LangGraph + Protocol Adapters (propose only)")
    pdf.bullet("TRUSTED: Control Plane, integer math, scoped authorization, payment clients")


def build_protocol_gateway(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("2. Protocol Gateway (V4)")

    pdf.body_text(
        "The Protocol Gateway is the single front door for agentic-commerce protocols. "
        "It authenticates the caller, validates schema, maps protocol-specific messages to a "
        "Normalized AegisPay Intent, and forwards that intent to the existing Control Plane. "
        "No adapter calls Razorpay, UPI, or x402 directly."
    )

    x, w, cx = 15, 180, 105
    y = pdf.get_y() + 2

    # Ingress protocols
    pdf.draw_layer_box(x, y, w, 26, "PROTOCOL INGRESS (external)", [
        "UCP  |  ACP  |  AP2  |  A2A  |  MCP  |  x402  |  A2UI  |  NPCI UAP / UPI",
    ], LIGHT_TEAL, TEAL)
    pdf.draw_arrow_down(cx, y + 26, y + 32)
    y += 32

    pdf.draw_layer_box(x, y, w, 22, "PROTOCOL GATEWAY (FastAPI edge)", [
        "AuthN  |  Agent identity  |  Schema validation  |  Replay / idempotency keys",
    ], LIGHT_GRAY, GRAY)
    pdf.draw_arrow_down(cx, y + 22, y + 28)
    y += 28

    pdf.draw_layer_box(x, y, w, 30, "PROTOCOL ADAPTERS (thin translators)", [
        "UCP Adapter  |  ACP Adapter  |  AP2 Adapter  |  MCP Tool Adapter  |  ...",
        "Output: Normalized AegisPay Intent (cart, amount, actor, protocol_ref)",
    ], LIGHT_BLUE, BLUE)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*RED)
    pdf.set_xy(x + 3, y + 30)
    pdf.cell(w, 4, "UNTRUSTED UNTIL CONTROL PLANE APPROVES")
    pdf.draw_arrow_down(cx, y + 34, y + 40)
    y += 40

    pdf.draw_layer_box(x, y, w, 22, "NORMALIZED AEGISPAY INTENT", [
        "tenant_id  |  agent_id  |  line_items  |  proposed_discount  |  protocol_metadata",
    ], (255, 251, 235), AMBER)
    pdf.draw_arrow_down(cx, y + 22, y + 28)
    y += 28

    pdf.draw_layer_box(x, y, w, 28, "AEGISPAY CONTROL PLANE (unchanged core)", [
        "Policy Engine  ->  Risk Scorer  ->  Authorization  ->  Inventory lock",
    ], LIGHT_GREEN, GREEN)
    pdf.draw_arrow_down(cx, y + 28, y + 34)
    y += 34

    pdf.draw_layer_box(x, y, w, 22, "PAYMENT EXECUTION (single rail router)", [
        "Razorpay Payment Links  |  UPI (future)  |  x402 (experimental)",
    ], LIGHT_RED, RED)
    pdf.draw_arrow_down(cx, y + 22, y + 28)
    y += 28

    pdf.draw_layer_box(x, y, w, 20, "SETTLEMENT + AUDIT", [
        "Webhooks  |  Reconciliation  |  Transaction Passport  |  audit_events",
    ], (254, 226, 226), RED)
    pdf.set_y(y + 26)


def build_protocol_catalog(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("3. Protocol Catalog & Maturity")

    pdf.body_text(
        "Status reflects architecture support today - not marketing claims. "
        "Core = in the live path. Adapter-ready = gateway contract defined, adapter can ship. "
        "Experimental = stub only. Future = planned rail, no adapter code yet."
    )

    headers = ["Protocol", "Role", "Status", "AegisPay mapping"]
    col_w = [22, 48, 28, 82]
    rows = [
        ("MCP", "Controlled agent tools & context", "Core", "Tool allowlist on agents table; MCP server maps to approved tools only"),
        ("ACP", "Agentic checkout / commerce", "Core", "Same as SELL path: cart -> guardrail -> pay"),
        ("A2UI", "Agent-driven UI components", "Adapter-ready", "Split UI: chat + trace; A2UI renders into hosted surfaces"),
        ("UCP", "Commerce interoperability", "Adapter-ready", "Adapter normalizes catalog/cart to AegisPay Intent"),
        ("AP2", "Payment mandates & verifiable auth", "Adapter-ready", "Maps to authorizations table (scoped, expiring, single-use)"),
        ("A2A", "Agent-to-agent messaging", "Experimental", "Gateway accepts A2A envelope; no production adapter yet"),
        ("x402", "Machine / pay-per-use HTTP payments", "Experimental", "Rail router stub; does not bypass Control Plane"),
        ("NPCI UAP", "India unified agentic payments", "Future", "Architecture slot in rail router; no live integration"),
        ("UPI", "India instant payments", "Future", "Settlement rail behind same authorization object"),
    ]
    pdf.simple_table(headers, rows, col_w)

    pdf.ln(4)
    pdf.subsection_title("What we do NOT do")
    pdf.bullet("Separate payment systems per protocol")
    pdf.bullet("LLM-direct Razorpay / UPI / x402 calls")
    pdf.bullet("Protocol-specific policy engines")
    pdf.bullet("Claiming live UPI/x402 until adapter + rail tests pass")


def build_system_architecture(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("4. System Architecture")

    pdf.body_text(
        "Client and protocol traffic enters through the API Gateway. LangGraph runs GROW and SELL. "
        "The Control Plane is protocol-agnostic. PostgreSQL + RLS hold catalog, orders, and audit. "
        "Redis handles locks, rate limits, and trace pub/sub."
    )

    x, w = 15, 180
    y = pdf.get_y() + 2

    pdf.draw_layer_box(x, y, w, 20, "CLIENT + A2UI SURFACES (Next.js)", [
        "Chat UI  |  Trace / Audit UI  |  Agent-rendered components (adapter-ready)",
    ], (224, 231, 255), BLUE)
    pdf.draw_arrow_down(x + w / 2, y + 20, y + 26)
    y += 26

    pdf.draw_layer_box(x, y, w, 24, "AEGISPAY API + PROTOCOL GATEWAY", [
        "REST / WS  |  Protocol ingress  |  Auth  |  Rate limits  |  Schema validation",
    ], LIGHT_GRAY, GRAY)
    pdf.draw_arrow_down(x + w / 2, y + 24, y + 30)
    y += 30

    pdf.draw_layer_box(x, y, w, 34, "AI ORCHESTRATION (LangGraph)", [
        "GROW: intent, catalog, upsell",
        "SELL: cart, price (int paise), checkout intent",
    ], (243, 232, 255), PURPLE)
    pdf.draw_arrow_down(x + w / 2, y + 34, y + 40)
    y += 40

    pdf.draw_layer_box(x, y, w, 30, "CONTROL PLANE (deterministic)", [
        "Policy  |  Risk  |  Authorization  |  HITL escalation",
    ], LIGHT_GREEN, GREEN)
    y += 34

    half = w / 2 - 2
    pdf.draw_layer_box(x, y, half, 28, "PAYMENT RAIL ROUTER", [
        "Razorpay (core)",
        "UPI / x402 (stubs)",
    ], LIGHT_RED, RED)
    pdf.draw_layer_box(x + half + 4, y, half, 28, "GRACEFUL FALLBACK", [
        "Halt money action",
        "Human ticket",
    ], (254, 243, 199), AMBER)
    pdf.draw_arrow_down(x + w / 4, y + 28, y + 34)
    y += 34

    pdf.draw_layer_box(x, y, w, 22, "DATA (PostgreSQL RLS + Redis + AWS)", [
        "Postgres: catalog, orders, audit_events  |  Redis: locks, trace  |  S3/Secrets/SQS",
    ], (254, 226, 226), RED)


def build_workflows(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("5. Workflow: GROW, SELL, PROTECT")

    pdf.body_text("Lifecycle stays linear. Protocol origin does not shorten the path.")

    pdf.subsection_title("Phase 1 - GROW")
    for i, (t, d) in enumerate([
        ("Intent", "Buyer or external agent requests a product, bundle, or discount."),
        ("Catalog", "Postgres search - not LLM hallucination."),
        ("Upsell", "Agent proposes bundle or discount percentage (not final price)."),
        ("Intent emit", "Adapter or LangGraph emits Normalized AegisPay Intent."),
    ], 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*BLUE)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {t}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, d)
        pdf.ln(1)

    pdf.subsection_title("Phase 2 - PROTECT")
    for i, (t, d) in enumerate([
        ("Inventory lock", "Redis + Postgres hold; fail closed if stock unavailable."),
        ("Policy", "Max discount, margin floor, qty caps - Python only."),
        ("Risk", "Injection patterns, velocity, anomaly score."),
        ("Authorization", "Scoped permission bound to cart hash + amount; single-use."),
    ], 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*GREEN)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {t}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, d)
        pdf.ln(1)

    pdf.subsection_title("Phase 3 - SELL")
    for i, (t, d) in enumerate([
        ("User confirm", "Explicit confirm - LangGraph interrupt; not inferred from chat."),
        ("Idempotent order", "Idempotency key per offer version; cart hash bound."),
        ("Payment link", "Razorpay POST /v1/payment_links (test or live)."),
        ("Webhook", "HMAC verify, dedupe event_id, amount match, mark paid."),
        ("Passport", "audit_events + order snapshot for replay."),
    ], 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*ORANGE)
        pdf.set_x(18)
        pdf.cell(0, 5, f"{i}. {t}")
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_x(22)
        pdf.multi_cell(168, 4.5, d)
        pdf.ln(1)

    pdf.ln(2)
    pdf.set_font("Courier", "B", 8)
    pdf.set_text_color(*NAVY)
    pdf.set_x(18)
    pdf.multi_cell(175, 5, "Protocol -> Gateway -> Intent -> GROW/SELL -> PROTECT -> [APPROVED] -> Pay -> Settle")


def build_payment_state_machine(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("6. Payment State Machine")

    pdf.body_text(
        "Same state machine for all rails. UNKNOWN is a real state - never blind retry."
    )

    headers = ["State", "Next", "Notes"]
    col_w = [40, 55, 85]
    rows = [
        ("created", "payment_pending", "Order + authorization issued"),
        ("payment_pending", "captured", "Webhook or poll confirms pay"),
        ("captured", "completed", "Reconciliation OK"),
        ("payment_pending", "unknown", "Timeout / no webhook"),
        ("unknown", "reconciliation", "Ask provider truth"),
        ("unknown", "captured / failed", "After reconcile"),
        ("authorization", "approved -> consumed", "Single-use scope"),
        ("authorization", "expired / revoked", "No pay allowed"),
    ]
    pdf.simple_table(headers, rows, col_w)

    pdf.ln(4)
    pdf.subsection_title("Webhook + reconciliation (all rails)")
    pdf.bullet("Verify signature (HMAC for Razorpay)")
    pdf.bullet("Dedupe by provider_event_id")
    pdf.bullet("Amount must match authorized amount_minor")
    pdf.bullet("Mismatch -> disputed state, HITL P0, no auto-complete")
    pdf.bullet("Outbox worker publishes events; reconciliation worker resolves UNKNOWN")


def build_protocol_security(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("7. Protocol Gateway Security")

    pdf.body_text(
        "Every protocol adapter runs the same security checks before the Control Plane sees data."
    )

    headers = ["Control", "Gateway enforcement"]
    col_w = [45, 135]
    rows = [
        ("Authentication", "mTLS or signed JWT per protocol; no anonymous money ingress"),
        ("Agent identity", "agents table: type, scopes, allowed_tools, trust_level, expiry"),
        ("Schema validation", "Pydantic / JSON Schema per adapter; reject malformed payloads"),
        ("Replay protection", "nonce + timestamp window; idempotency_keys per scope"),
        ("Scoped authorization", "AP2 mandates map to authorizations; bound to cart_hash + amount"),
        ("Idempotency", "Same key returns same result; no double spend"),
        ("Tool allowlisting", "MCP tools must appear in agents.allowed_tools JSONB"),
        ("Tenant isolation", "tenant_id on every row; Postgres RLS; SET LOCAL per request"),
    ]
    pdf.simple_table(headers, rows, col_w)


def build_security_matrix(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("8. Hardcoded Guardrails")

    pdf.body_text("No protocol or LLM prompt overrides these. Enforced in Control Plane code.")

    headers = ["Vector", "Threat", "AegisPay response"]
    col_w = [36, 44, 100]
    rows = [
        ("Cross-tenant leak", "Protocol sends wrong tenant", "RLS + server-set tenant context"),
        ("Double spend", "Retry after timeout", "Idempotency + never-retry-unknown"),
        ("Margin erosion", "Injection / fake discount", "RULE_MIN_MARGIN + price floor"),
        ("Oversell", "Two agents, one SKU", "Redis lock + FOR UPDATE holds"),
        ("Untraceable pay", "Dispute with no proof", "Transaction Passport from audit_events"),
        ("Forged webhook", "Fake paid event", "HMAC + event dedupe + amount check"),
        ("LLM math", "Wrong total", "Integer paise in compute_totals only"),
        ("Protocol bypass", "Adapter calls Razorpay", "Adapters cannot hold payment credentials"),
    ]
    pdf.simple_table(headers, rows, col_w)

    pdf.ln(4)
    pdf.subsection_title("Authorization outcomes")
    for label, desc, color in [
        ("AUTO-APPROVE", "Low risk, in policy, stock OK", GREEN),
        ("CLAMP", "Discount over cap - reduce to max", ORANGE),
        ("REJECT", "Margin or stock fail", RED),
        ("HUMAN", "High risk, max rounds, engine error", PURPLE),
    ]:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*color)
        pdf.set_x(18)
        pdf.cell(36, 5, label)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.multi_cell(144, 4.5, desc)


def build_passport_and_aws(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("9. Transaction Passport & Audit")

    pdf.body_text(
        "No separate passport table. Built from order, authorization, policy version, "
        "payment, and hash-chained audit_events. Append-only. Tamper-evident."
    )

    pdf.subsection_title("Passport sources")
    for field, desc in [
        ("protocol_ref", "Which adapter / protocol originated the intent"),
        ("session_id", "Agent session or LangGraph thread"),
        ("authorization_id", "Scoped approval used for payment"),
        ("policy_version", "Ruleset that ran"),
        ("grow_trace", "Intent, catalog, negotiation"),
        ("protect_trace", "Per-rule pass / clamp / reject"),
        ("sell_trace", "Rail, idempotency_key, webhook ids"),
        ("amount_minor", "Final integer amount"),
    ]:
        pdf.set_font("Courier", "B", 8)
        pdf.set_text_color(*BLUE)
        pdf.set_x(18)
        pdf.cell(42, 5, field)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 5, desc, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.subsection_title("Observability")
    pdf.bullet("WebSocket trace:{session_id} - live node + rule events")
    pdf.bullet("audit_events - durable ledger")
    pdf.bullet("Redis pub/sub - fan-out to trace UI")

    pdf.ln(2)
    pdf.section_title("10. AWS Deployment")

    pdf.body_text("Monolithic API first. Workers for webhooks and reconciliation. No per-protocol stacks.")

    headers = ["Layer", "AWS service", "Purpose"]
    col_w = [40, 45, 95]
    rows = [
        ("Edge", "ALB + WAF", "TLS termination, rate limits"),
        ("Compute", "ECS Fargate / EKS", "FastAPI + LangGraph + Protocol Gateway"),
        ("Data", "RDS PostgreSQL", "RLS catalog, orders, audit_events"),
        ("Cache", "ElastiCache Redis", "Locks, sessions, trace pub/sub"),
        ("Secrets", "Secrets Manager", "Razorpay keys - never in DB or LLM"),
        ("Async", "SQS + workers", "webhook_events, outbox, reconciliation"),
        ("Static", "S3 + CloudFront", "A2UI assets, exports"),
        ("Logs", "CloudWatch", "Structured audit correlation_id"),
    ]
    pdf.simple_table(headers, rows, col_w)

    pdf.ln(4)
    pdf.subsection_title("Human-in-the-loop")
    pdf.bullet("escalation_tickets for ESCALATED guardrail outcomes")
    pdf.bullet("Human override logged with actor=human; margin floor not overridden in v1")
    pdf.bullet("Payment disputed (webhook mismatch) -> P0 queue")


def build_closing(pdf: AegisPayPDF):
    pdf.add_page()
    pdf.section_title("11. V4 Summary")

    pdf.body_text(
        "AegisPay V4 welcomes the agentic-commerce protocol ecosystem through one Protocol Gateway "
        "and one Control Plane. GROW and SELL are unchanged in spirit. PROTECT is unchanged in law: "
        "policy, risk, and authorization run in deterministic Python before any rail is called."
    )

    pdf.subsection_title("Implementation truth (Aug 2026)")
    pdf.bullet("Core today: MCP tool gating, ACP/SELL checkout, Razorpay, audit, RLS, HITL design")
    pdf.bullet("Adapter-ready: UCP, AP2, A2UI gateway contracts documented")
    pdf.bullet("Experimental: A2A ingress envelope, x402 rail stub")
    pdf.bullet("Future: NPCI UAP / UPI settlement adapters")

    pdf.ln(6)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_x(15)
    pdf.multi_cell(180, 7, (
        "Different protocols can enter AegisPay,\n"
        "but none can bypass the AegisPay Control Plane."
    ), align="C")


def main():
    pdf = AegisPayPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)

    build_cover(pdf)
    build_philosophy(pdf)
    build_protocol_gateway(pdf)
    build_protocol_catalog(pdf)
    build_system_architecture(pdf)
    build_workflows(pdf)
    build_payment_state_machine(pdf)
    build_protocol_security(pdf)
    build_security_matrix(pdf)
    build_passport_and_aws(pdf)
    build_closing(pdf)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    paths = [OUTPUT, OUTPUT_DL, OUTPUT.with_name(OUTPUT.stem + "_latest.pdf")]
    written = []
    for path in paths:
        try:
            pdf.output(str(path))
            written.append(path)
            print(f"PDF generated: {path}")
        except PermissionError:
            print(f"Skipped (locked): {path}")
    if not written:
        raise SystemExit("Could not write PDF to any path")


if __name__ == "__main__":
    main()
