# KeenPay

Agentic checkout with a hard boundary between what the AI can *suggest* and what the system is allowed to *charge*.

Stack: FastAPI, LangGraph, PostgreSQL, Redis, Next.js, Razorpay (test mode for dev).

## What it does

Three jobs, one pipeline:

- **Grow** — cross-sell, upsell, bundle suggestions from catalog + purchase context
- **Sell** — buyer finds a product, negotiates, confirms, pays via Razorpay Payment Link
- **Protect** — every discount, hold, and payment call passes deterministic Python rules first

The rule we do not break:

> The LLM proposes. The policy engine approves. Only then does Razorpay get called.

No API keys in the model context. No `create_payment_link` tool on the agent. Payment is a gated side effect, not a model decision.

## Repo layout

```
docs/
  PRD.md                  product scope and acceptance criteria
  ARCHITECTURE.md         LangGraph graph, services, data flow
  GUARDRAILS_AND_SAFETY.md  policy rules, failure matrix, HITL
  AI_JUDGMENT.md          what the LLM does vs what Python does
  API_SPEC.md             REST, WebSocket, webhook contracts
  SCHEMA.sql              PostgreSQL DDL (run this for local DB)
  DEVELOPMENT_LOG.md      bugs and fixes as we build
scripts/
  generate_architecture_pdf.py
  generate_database_schema_pdf.py
```

Implementation folders (`api/`, `frontend/`, `workers/`) are next — specs above are the source of truth until code lands.

## Checkout flow (short)

```
chat message
  -> parse_intent (LLM)
  -> catalog_search (Postgres full-text, not LLM)
  -> negotiate_offer (LLM proposes discount % only)
  -> guardrail_check (PolicyEngine — sync Python, no LLM)
  -> compute_totals (integer paise math)
  -> await_user_confirmation (LangGraph interrupt)
  -> create_payment_link (Razorpay, idempotent)
  -> webhook marks order paid
```

Trace events stream over WebSocket to the right-hand panel while `audit_logs` stores the durable record.

## Non-negotiables

These are enforced in code, not in prompts:

| Rule | How |
|------|-----|
| Max discount | `RULE_MAX_DISCOUNT` clamps or rejects |
| Margin floor | `RULE_MIN_MARGIN` uses `products.cost_paise` |
| Stock | `RULE_INVENTORY_AVAILABLE` + `inventory_holds` |
| No blind retry on timeout | order stays pending/unknown; reconcile against Razorpay |
| Idempotency | `(session_id, offer_version)` on payment links; `event_id` on webhooks |
| Audit | append-only `audit_logs` with DB trigger blocking UPDATE/DELETE |

Full rule list: `docs/GUARDRAILS_AND_SAFETY.md`.

## Transaction passport

There is no separate passport table. Support replay is built from:

`negotiation_sessions` + `orders` + `audit_logs` + `webhook_events`

The UI trace panel is live; the database is what finance trusts.

## Local setup (once code exists)

```bash
# database
psql $DATABASE_URL -f docs/SCHEMA.sql

# backend (planned)
cd api && uvicorn main:app --reload

# frontend (planned)
cd frontend && npm run dev
```

Razorpay test keys go in env — never in the repo or LLM prompts.

## Docs worth reading first

1. `docs/PRD.md` — what ships in v1
2. `docs/ARCHITECTURE.md` — graph nodes and state shape
3. `docs/AI_JUDGMENT.md` — if you are wiring the agent, read this before adding tools
