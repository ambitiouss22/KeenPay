#!/usr/bin/env python3
"""Build docs/KeenPay_Database_Schema.pdf from docs/SCHEMA.sql (canonical source)."""

from pathlib import Path
from fpdf import FPDF

OUTPUT_DOCS = Path(__file__).resolve().parent.parent / "docs" / "KeenPay_Database_Schema.pdf"
OUTPUT_DOWNLOADS = Path.home() / "Downloads" / "KeenPay-Database-Schema.pdf"

NAVY = (15, 23, 42)
BLUE = (37, 99, 235)
LIGHT_BLUE = (219, 234, 254)
GREEN = (22, 163, 74)
LIGHT_GREEN = (220, 252, 231)
ORANGE = (234, 88, 12)
LIGHT_ORANGE = (255, 237, 213)
GRAY = (100, 116, 139)
LIGHT_GRAY = (241, 245, 249)
WHITE = (255, 255, 255)
RED = (220, 38, 38)
PURPLE = (124, 58, 237)


class KeenPaySchemaPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, "KeenPay - Production Database Schema", align="C")
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

    def table_card(self, name: str, purpose: str, columns: list[tuple], fks: list[str], indexes: list[str], access: list[tuple]):
        if self.get_y() > 200:
            self.add_page()
        self.subsection_title(f"TABLE: {name}")
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*NAVY)
        self.cell(0, 5, "Purpose")
        self.ln(4)
        self.body_text(purpose)

        col_w = [42, 28, 110]
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 7)
        for h, w in zip(["Column", "Type", "Meaning"], col_w):
            self.cell(w, 6, h, border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 7)
        for idx, (col, typ, meaning) in enumerate(columns):
            fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
            self.set_fill_color(*fill)
            self.set_text_color(30, 41, 59)
            self.cell(col_w[0], 5, col, border=1, fill=True)
            self.cell(col_w[1], 5, typ, border=1, fill=True)
            self.cell(col_w[2], 5, meaning, border=1, fill=True)
            self.ln()

        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*NAVY)
        self.cell(0, 5, "Foreign Keys")
        self.ln(4)
        for fk in fks:
            self.bullet(fk, indent=18)
        self.ln(1)

        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*NAVY)
        self.cell(0, 5, "Important Indexes")
        self.ln(4)
        for idx in indexes:
            self.bullet(idx, indent=18)
        self.ln(1)

        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*NAVY)
        self.cell(0, 5, "Service Access")
        self.ln(4)
        for service, allowed in access:
            mark = "yes" if allowed else "no direct access"
            self.bullet(f"{service}: {mark}", indent=18)
        self.ln(3)


def build_cover(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")
    pdf.set_y(55)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 12, "KeenPay", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*LIGHT_BLUE)
    pdf.cell(0, 10, "Production Database Schema", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(203, 213, 225)
    pdf.cell(0, 6, "Simple, secure PostgreSQL design for agentic commerce.", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "AI proposes. The control plane authorizes. Money never bypasses policy.", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(16)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(148, 163, 184)
    pdf.cell(0, 6, "Grow  |  Sell  |  Protect", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "PostgreSQL 15  |  Integer Paise  |  Append-Only Audit", align="C")
    pdf.set_y(255)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 6, "Version 1.0.0  |  August 2026", align="C")


def build_overview(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("1. Database Overview")
    pdf.body_text(
        "KeenPay uses PostgreSQL as the source of truth for catalog, negotiation sessions, "
        "orders, and audit. v1 targets a single merchant pilot with merchant_id columns ready "
        "for multi-tenant expansion. The AI runtime reads catalog and writes session proposals; "
        "financial tables are owned by the deterministic control plane."
    )

    pdf.subsection_title("Table Groups")
    groups = [
        ("Catalog", "products"),
        ("Agentic Checkout", "negotiation_sessions, langgraph_checkpoints"),
        ("Commerce", "orders, inventory_holds"),
        ("Control & Safety", "escalation_tickets"),
        ("Payments", "webhook_events"),
        ("Audit", "audit_logs (append-only)"),
    ]
    col_w = [55, 125]
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(col_w[0], 7, "Group", border=1, fill=True)
    pdf.cell(col_w[1], 7, "Tables", border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for idx, (group, tables) in enumerate(groups):
        fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
        pdf.set_fill_color(*fill)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(col_w[0], 6, group, border=1, fill=True)
        pdf.cell(col_w[1], 6, tables, border=1, fill=True)
        pdf.ln()

    pdf.ln(4)
    pdf.subsection_title("Design Principles")
    pdf.bullet("Integer paise (BIGINT/INTEGER) for all money - no floats")
    pdf.bullet("AI may propose offers in negotiation_sessions; only policy-approved amounts reach orders")
    pdf.bullet("audit_logs is append-only; UPDATE/DELETE blocked by database trigger")
    pdf.bullet("Every payment link binds to guardrail_decision_id + offer_version")
    pdf.bullet("Webhook events deduplicated by unique event_id")


def build_architecture(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("2. Data Plane Architecture")
    pdf.body_text(
        "The schema enforces a strict split: LangGraph mirrors conversational state in "
        "negotiation_sessions and langgraph_checkpoints. Once guardrails approve and the user "
        "confirms, an immutable order row is created. Payment lifecycle completes via Razorpay "
        "webhooks stored in webhook_events."
    )

    layers = [
        ("UNTRUSTED INPUT", "User chat, LLM proposals, inbound webhooks", LIGHT_ORANGE, ORANGE),
        ("LANGGRAPH STATE", "negotiation_sessions, langgraph_checkpoints", LIGHT_BLUE, BLUE),
        ("CONTROL PLANE", "Policy binding via guardrail_decision_id on orders", LIGHT_GREEN, GREEN),
        ("DURABLE AUDIT", "audit_logs, webhook_events, escalation_tickets", (254, 226, 226), RED),
    ]
    y = pdf.get_y() + 2
    for title, desc, fill, border in layers:
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(*border)
        pdf.set_line_width(0.4)
        pdf.rect(15, y, 180, 16, style="DF")
        pdf.set_xy(18, y + 3)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*NAVY)
        pdf.cell(60, 4, title)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(115, 4, desc)
        y += 20
    pdf.set_y(y + 2)

    pdf.subsection_title("Entity Relationships (v1)")
    pdf.body_text(
        "products <- negotiation_sessions (via selected_line_items JSONB) -> orders -> webhook_events. "
        "audit_logs correlates session_id, order_id, and decision_id across the lifecycle. "
        "inventory_holds tie stock reservations to session_id before payment link creation."
    )


def build_catalog_tables(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("3. Catalog Tables")

    pdf.table_card(
        "products",
        "Merchant catalog. Price and cost basis are read by the policy engine for margin guardrails. "
        "The AI runtime may read active products but cannot mutate prices.",
        [
            ("id", "VARCHAR(64)", "Product ID"),
            ("sku", "VARCHAR(64)", "Stock keeping unit (unique per merchant)"),
            ("merchant_id", "VARCHAR(64)", "Merchant scope (default merchant_keen)"),
            ("name", "VARCHAR(255)", "Display name"),
            ("list_price_paise", "INTEGER", "List price in paise"),
            ("cost_paise", "INTEGER", "Cost basis for RULE_MIN_MARGIN"),
            ("quantity_on_hand", "INTEGER", "Physical stock"),
            ("quantity_reserved", "INTEGER", "Reserved by active holds"),
            ("attributes", "JSONB", "Category, color, size, etc."),
            ("search_vector", "TSVECTOR", "Full-text search index (generated)"),
            ("active", "BOOLEAN", "Catalog visibility"),
        ],
        ["tenant_id -> merchants (v1 implicit single merchant)"],
        ["(merchant_id, sku) UNIQUE", "GIN on search_vector", "GIN on attributes"],
        [
            ("AI Runtime (LangGraph)", True),
            ("Policy Engine", True),
            ("Payment Service", False),
            ("Webhook Worker", False),
        ],
    )


def build_session_tables(pdf: KeenPaySchemaPDF):
    pdf.section_title("4. Agentic Checkout Tables")

    pdf.table_card(
        "negotiation_sessions",
        "Live checkout session. Mirrors LangGraph KeenPayState: intent, offers, guardrail binding, "
        "and user confirmation gate. No payment link without APPROVED guardrail + user_confirmed_payment.",
        [
            ("id", "UUID", "Session ID (= LangGraph thread_id)"),
            ("status", "ENUM", "active | negotiating | awaiting_confirmation | payment_pending | paid | escalated | closed"),
            ("negotiation_round", "INTEGER", "Bounded negotiation counter (max 5)"),
            ("offer_version", "INTEGER", "Monotonic offer version"),
            ("parsed_intent", "JSONB", "Structured intent from parse_intent node"),
            ("proposed_offer", "JSONB", "LLM-proposed offer (untrusted until guardrail)"),
            ("approved_offer", "JSONB", "Policy-approved offer snapshot"),
            ("guardrail_decision", "ENUM", "APPROVED | REJECTED | ESCALATED"),
            ("guardrail_decision_id", "UUID", "Links to audit_logs decision"),
            ("guardrail_detail", "JSONB", "Per-rule evaluation results"),
            ("user_confirmed_payment", "BOOLEAN", "Explicit user consent gate"),
            ("final_amount_paise", "INTEGER", "Deterministic total (post compute_totals)"),
            ("security_block", "BOOLEAN", "True after prompt injection / anomaly block"),
            ("langgraph_thread_id", "UUID", "LangGraph checkpointer reference"),
        ],
        ["langgraph_thread_id conventionally equals id"],
        ["(user_id, created_at DESC)", "(status) partial index", "(guardrail_decision_id)"],
        [
            ("AI Runtime (LangGraph)", True),
            ("Policy Engine", True),
            ("Payment Service", False),
            ("Merchant Dashboard", True),
        ],
    )

    pdf.table_card(
        "langgraph_checkpoints",
        "LangGraph durable state snapshots for conversation recovery and interrupt/resume.",
        [
            ("thread_id", "UUID", "Session thread"),
            ("checkpoint_id", "UUID", "Checkpoint version"),
            ("checkpoint", "JSONB", "Serialized graph state"),
            ("metadata", "JSONB", "Node hints, timestamps"),
        ],
        ["thread_id -> negotiation_sessions.langgraph_thread_id"],
        ["(thread_id, created_at DESC)"],
        [
            ("AI Runtime (LangGraph)", True),
            ("Policy Engine", False),
            ("Payment Service", False),
        ],
    )


def build_commerce_tables(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("5. Commerce & Inventory Tables")

    pdf.table_card(
        "orders",
        "Frozen purchase created only after guardrail APPROVED + user confirmation. "
        "Amounts and line_items are immutable after insert. Binds to Razorpay payment link.",
        [
            ("id", "VARCHAR(64)", "Order ID"),
            ("session_id", "UUID", "Source negotiation session"),
            ("status", "ENUM", "pending | paid | expired | cancelled | payment_disputed"),
            ("subtotal_paise", "INTEGER", "Pre-discount subtotal"),
            ("discount_amount_paise", "INTEGER", "Applied discount"),
            ("final_amount_paise", "INTEGER", "Charge amount (must match webhook)"),
            ("line_items", "JSONB", "Immutable line snapshot at order time"),
            ("guardrail_decision_id", "UUID", "Required policy evaluation reference"),
            ("offer_version", "INTEGER", "Offer version that created this order"),
            ("policy_version", "VARCHAR(32)", "Policy ruleset version"),
            ("razorpay_payment_link_id", "VARCHAR(64)", "Razorpay entity"),
            ("razorpay_payment_link_url", "TEXT", "Checkout URL for buyer"),
            ("idempotency_key", "VARCHAR(128)", "Duplicate protection (UNIQUE)"),
        ],
        [
            "session_id -> negotiation_sessions.id",
            "guardrail_decision_id -> audit trail (logical)",
        ],
        [
            "(session_id)",
            "(razorpay_payment_link_id) partial",
            "UNIQUE (idempotency_key)",
            "UNIQUE pending order per session",
        ],
        [
            ("AI Runtime", False),
            ("Payment Service", True),
            ("Webhook Worker", True),
            ("Reconciliation Worker", True),
        ],
    )

    pdf.table_card(
        "inventory_holds",
        "Durable stock reservation during checkout. Complements Redis soft holds. "
        "Released on expiry/cancel; consumed on payment capture.",
        [
            ("id", "UUID", "Hold ID"),
            ("session_id", "UUID", "Checkout session"),
            ("product_id", "VARCHAR(64)", "Product reserved"),
            ("sku", "VARCHAR(64)", "SKU for guardrail reads"),
            ("quantity", "INTEGER", "Reserved quantity"),
            ("expires_at", "TIMESTAMPTZ", "Auto-release time (15 min default)"),
            ("released_at", "TIMESTAMPTZ", "Null while active"),
        ],
        [
            "session_id -> negotiation_sessions.id",
            "product_id -> products.id",
        ],
        ["(expires_at) WHERE released_at IS NULL", "UNIQUE (session_id, sku)"],
        [
            ("AI Runtime", False),
            ("Policy Engine", True),
            ("Payment Service", True),
        ],
    )


def build_control_tables(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("6. Control, Payments & Audit Tables")

    pdf.table_card(
        "escalation_tickets",
        "Human-in-the-loop queue when guardrails ESCALATE (anomaly, max rounds, engine error). "
        "Human overrides are logged with actor=human; margin floor is never overridden in v1.",
        [
            ("id", "UUID", "Ticket ID"),
            ("session_id", "UUID", "Blocked session"),
            ("priority", "VARCHAR(4)", "P0 | P1 | P2"),
            ("reason_code", "VARCHAR(64)", "e.g. RULE_SECURITY_ANOMALY"),
            ("status", "VARCHAR(16)", "open | assigned | resolved | expired"),
            ("proposed_offer_snapshot", "JSONB", "Offer under review"),
            ("policy_snapshot", "JSONB", "Policy at escalation time"),
            ("resolution", "VARCHAR(32)", "approve_override | deny | counter_offer"),
            ("override_discount_pct", "NUMERIC(5,2)", "Manager override (bounded)"),
        ],
        ["session_id -> negotiation_sessions.id"],
        ["(status, priority, created_at)"],
        [
            ("AI Runtime", False),
            ("Control Plane / Admin API", True),
            ("Merchant Dashboard", True),
        ],
    )

    pdf.table_card(
        "webhook_events",
        "Razorpay inbound events. Signature verified on receipt; deduplicated by event_id. "
        "Amount mismatch marks order payment_disputed - never auto-paid.",
        [
            ("id", "UUID", "Internal event ID"),
            ("event_id", "VARCHAR(128)", "Razorpay event ID (UNIQUE)"),
            ("event_type", "VARCHAR(64)", "e.g. payment_link.paid"),
            ("payload", "JSONB", "Raw webhook body"),
            ("signature_valid", "BOOLEAN", "HMAC verification result"),
            ("processed", "BOOLEAN", "Worker completion flag"),
            ("order_id", "VARCHAR(64)", "Matched order"),
        ],
        ["order_id -> orders.id"],
        ["UNIQUE (event_id)", "(event_type, received_at DESC)", "unprocessed index"],
        [
            ("AI Runtime", False),
            ("Webhook Worker", True),
            ("Payment Service", False),
        ],
    )

    pdf.table_card(
        "audit_logs",
        "Append-only tamper-evident ledger. Every money action and guardrail evaluation "
        "writes a row with input_snapshot and output_snapshot. UPDATE/DELETE blocked by trigger.",
        [
            ("id", "UUID", "Audit row ID"),
            ("session_id", "UUID", "Negotiation session"),
            ("order_id", "VARCHAR(64)", "Order if applicable"),
            ("actor", "ENUM", "agent | policy_engine | user | system | webhook | human"),
            ("action", "VARCHAR(128)", "e.g. GUARDRAIL_EVALUATED, PAYMENT_LINK_CREATED"),
            ("decision_id", "UUID", "Guardrail evaluation reference"),
            ("offer_version", "INTEGER", "Bound offer version"),
            ("input_snapshot", "JSONB", "Proposed offer, policy version, message hash"),
            ("output_snapshot", "JSONB", "Decision, rule results, payment link ID"),
            ("trace_metadata", "JSONB", "Node name, duration_ms, anomaly score"),
        ],
        [
            "session_id -> negotiation_sessions.id",
            "order_id -> orders.id",
        ],
        [
            "(session_id, created_at DESC)",
            "(decision_id) partial",
            "GIN on trace_metadata",
        ],
        [
            ("AI Runtime", False),
            ("Policy Engine", True),
            ("Merchant Dashboard (read)", True),
            ("Webhook Worker", True),
        ],
    )


def build_states_and_flow(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("7. State Transitions")

    states = [
        ("negotiation_sessions.status", [
            ("active", "negotiating"),
            ("negotiating", "awaiting_confirmation (guardrail APPROVED)"),
            ("awaiting_confirmation", "payment_pending (user confirmed)"),
            ("payment_pending", "paid (webhook verified)"),
            ("*", "escalated (guardrail ESCALATED)"),
            ("*", "closed (terminal)"),
        ]),
        ("orders.status", [
            ("pending", "paid (webhook amount match)"),
            ("pending", "expired (link TTL)"),
            ("pending", "cancelled"),
            ("pending", "payment_disputed (amount mismatch)"),
        ]),
        ("guardrail_decision", [
            ("proposed offer", "APPROVED -> compute_totals"),
            ("proposed offer", "REJECTED -> explain + retry (max 5 rounds)"),
            ("proposed offer", "ESCALATED -> escalation_tickets"),
        ]),
    ]

    for title, rows in states:
        pdf.subsection_title(title)
        col_w = [55, 125]
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_w[0], 6, "From", border=1, fill=True)
        pdf.cell(col_w[1], 6, "To / Action", border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for idx, (frm, to) in enumerate(rows):
            fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
            pdf.set_fill_color(*fill)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(col_w[0], 5, frm, border=1, fill=True)
            pdf.cell(col_w[1], 5, to, border=1, fill=True)
            pdf.ln()
        pdf.ln(3)

    pdf.subsection_title("SELL Database Flow")
    flow_steps = [
        "1. User message -> negotiation_sessions updated (proposed_offer)",
        "2. Policy engine -> guardrail_decision + audit_logs row",
        "3. User confirms -> inventory_holds + orders (pending) + Razorpay link",
        "4. Webhook -> webhook_events + orders.status=paid + audit_logs",
    ]
    for step in flow_steps:
        pdf.bullet(step)


def build_passport_and_checklist(pdf: KeenPaySchemaPDF):
    pdf.add_page()
    pdf.section_title("8. Transaction Passport (Derived View)")
    pdf.body_text(
        "There is no separate passport table. The Transaction Passport is assembled from "
        "negotiation_sessions, orders, audit_logs, and webhook_events for support replay "
        "and compliance. The frontend trace panel streams real-time events via Redis; "
        "audit_logs is the durable source of truth."
    )

    pdf.subsection_title("Source of Truth")
    truths = [
        ("Product price", "products.list_price_paise"),
        ("Negotiated unit price", "approved_offer in negotiation_sessions"),
        ("Final charge amount", "orders.final_amount_paise"),
        ("Policy evaluation", "audit_logs where action=GUARDRAIL_EVALUATED"),
        ("Payment status", "orders.status + webhook_events"),
        ("Provider truth", "Razorpay API + verified webhook"),
        ("Hot session cache", "Redis (never authoritative for money)"),
    ]
    col_w = [50, 130]
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(col_w[0], 6, "Thing", border=1, fill=True)
    pdf.cell(col_w[1], 6, "Where it lives", border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for idx, (thing, where) in enumerate(truths):
        fill = LIGHT_GRAY if idx % 2 == 0 else WHITE
        pdf.set_fill_color(*fill)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(col_w[0], 6, thing, border=1, fill=True)
        pdf.cell(col_w[1], 6, where, border=1, fill=True)
        pdf.ln()

    pdf.ln(4)
    pdf.section_title("9. Production Checklist")
    checklist = [
        "All money stored as integer paise - no floating point",
        "Payment link requires guardrail_decision=APPROVED + user_confirmed_payment",
        "Idempotency key on every order and cached Razorpay link per offer version",
        "Webhook HMAC verified; duplicate event_id returns 200 without side effect",
        "Webhook amount must equal orders.final_amount_paise or mark payment_disputed",
        "audit_logs append-only (trigger blocks UPDATE/DELETE)",
        "Inventory: quantity_reserved <= quantity_on_hand constraint",
        "LLM never writes to orders or webhook_events - gated Python nodes only",
        "Negotiation capped at 5 rounds before ESCALATED -> escalation_tickets",
        "Secrets in environment / secrets manager - never in database or LLM context",
    ]
    for item in checklist:
        pdf.bullet(item)

    pdf.ln(4)
    pdf.section_title("10. v1.1 Roadmap (Not in Current DDL)")
    pdf.body_text(
        "The following enterprise tables are deferred to keep v1 deployable: separate tenants/users "
        "with RLS, standalone policies/authorizations tables, refunds, idempotency_keys cache table, "
        "transactional outbox, and GROW campaign tables. v1 encodes policy in MerchantPolicy config "
        "and guardrail snapshots in audit_logs JSONB."
    )


def main():
    pdf = KeenPaySchemaPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)

    build_cover(pdf)
    build_overview(pdf)
    build_architecture(pdf)
    build_catalog_tables(pdf)
    build_session_tables(pdf)
    build_commerce_tables(pdf)
    build_control_tables(pdf)
    build_states_and_flow(pdf)
    build_passport_and_checklist(pdf)

    OUTPUT_DOCS.parent.mkdir(parents=True, exist_ok=True)

    def write_pdf(path: Path) -> Path:
        fallback = path.with_name(path.stem + "_latest.pdf")
        for target in (path, fallback):
            try:
                pdf.output(str(target))
                return target
            except PermissionError:
                if target == fallback:
                    raise
                print(f"Could not overwrite {path} (file may be open); writing {fallback}")
        return path

    docs_path = write_pdf(OUTPUT_DOCS)
    dl_path = write_pdf(OUTPUT_DOWNLOADS)
    print(f"PDF generated: {docs_path}")
    print(f"PDF generated: {dl_path}")


if __name__ == "__main__":
    main()
