# KeenPay — production directory structure

Canonical layout for the KeenPay monorepo. Aligned with `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`, and `docs/PRD.md`.

**Design principles**

- **Money boundary** — policy, payment, and audit code live in explicit Python modules; never in LLM tool paths.
- **Thin edges, fat domain** — routers/WebSockets delegate to services; services use repositories for Postgres.
- **One source of truth for schema** — `docs/SCHEMA.sql` is the DDL reference; `db/migrations/` holds versioned deltas.
- **No cloud vendor lock-in** — Docker Compose for local/staging; deploy targets (Railway, Fly, Vercel) via env, not IaC.
- **Colocated tests** — unit/integration beside each app; cross-service flows in `tests/e2e/`.

---

## Tree

```
KeenPay/
├── .github/
│   └── workflows/              # CI: lint, test, build images
│       ├── api-ci.yml
│       ├── web-ci.yml
│       └── integration.yml
│
├── api/                        # FastAPI + LangGraph (Python 3.12+)
│   ├── main.py                 # uvicorn entry: main:app
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── README.md
│   │
│   ├── config/                 # Settings, logging, feature flags
│   │   ├── settings.py
│   │   └── logging.py
│   │
│   ├── core/                   # Cross-cutting primitives
│   │   ├── exceptions.py
│   │   ├── security.py         # JWT verify, payment gate asserts
│   │   └── idempotency.py
│   │
│   ├── dependencies/           # FastAPI Depends() wiring
│   │   ├── auth.py
│   │   ├── db.py
│   │   └── redis.py
│   │
│   ├── middleware/
│   │   ├── request_id.py
│   │   └── rate_limit.py
│   │
│   ├── routers/                # REST /api/v1/*
│   │   ├── health.py
│   │   ├── sessions.py
│   │   ├── catalog.py
│   │   ├── orders.py
│   │   ├── admin.py
│   │   ├── webhooks.py         # POST /webhooks/razorpay
│   │   └── dev.py              # Razorpay mock/simulate (non-prod)
│   │
│   ├── websockets/             # /ws/v1/session
│   │   ├── session.py
│   │   └── handlers.py
│   │
│   ├── graph/                  # LangGraph KeenPayStateGraph
│   │   ├── keen_checkout.py    # Graph definition + compile
│   │   ├── state.py            # KeenPayState, ProposedOffer, GuardrailDecision
│   │   ├── edges.py            # Conditional routing
│   │   └── nodes/
│   │       ├── parse_intent.py
│   │       ├── catalog_search.py
│   │       ├── negotiate_offer.py
│   │       ├── guardrail_check.py
│   │       ├── compute_totals.py
│   │       ├── await_confirmation.py
│   │       ├── create_payment_link.py
│   │       └── escalation.py
│   │
│   ├── policy/                 # Deterministic guardrails (no LLM)
│   │   ├── engine.py           # PolicyEngine.evaluate()
│   │   ├── anomaly.py          # Injection detection, security_block
│   │   └── rules/
│   │       ├── max_discount.py
│   │       ├── min_margin.py
│   │       └── inventory.py
│   │
│   ├── services/               # Business logic
│   │   ├── catalog.py
│   │   ├── session.py
│   │   ├── inventory.py
│   │   ├── razorpay.py         # Links + signature verify
│   │   ├── razorpay_mock.py    # Local dev without live keys
│   │   ├── audit.py            # append-only audit_logs
│   │   ├── trace.py            # Redis pub/sub trace events
│   │   └── llm.py              # LLM client (parse_intent, negotiate only)
│   │
│   ├── repositories/           # Postgres access layer
│   │   ├── orders.py
│   │   ├── sessions.py
│   │   ├── products.py
│   │   ├── audit.py
│   │   └── webhooks.py
│   │
│   ├── schemas/                # Pydantic request/response models
│   │   ├── common.py
│   │   ├── session.py
│   │   ├── catalog.py
│   │   ├── order.py
│   │   ├── offer.py
│   │   ├── trace.py
│   │   └── webhook.py
│   │
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       ├── integration/
│       └── fixtures/
│
├── frontend/                   # Next.js 14+ App Router
│   ├── package.json
│   ├── Dockerfile
│   ├── README.md
│   ├── public/
│   └── src/
│       ├── app/                # Routes: /, /session/[id]
│       ├── components/
│       │   ├── chat/           # Left panel — messages, product cards
│       │   ├── trace/          # Right panel — live graph/rule events
│       │   └── ui/             # Shared primitives
│       ├── hooks/              # useWebSocket, useSession
│       ├── lib/                # API client, auth helpers
│       ├── stores/             # Client state (session, trace buffer)
│       └── types/              # TS types mirroring api/schemas
│
├── workers/                    # Async background jobs
│   ├── main.py                 # Worker process entry
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── README.md
│   ├── config.py
│   ├── webhook_processor.py    # Idempotent webhook fan-out (ARCHITECTURE.md)
│   └── jobs/
│       ├── hold_expiry.py      # Release expired inventory_holds
│       └── reconciliation.py   # Razorpay timeout / unknown order sync
│
├── db/
│   ├── migrations/             # Alembic or numbered SQL migrations
│   │   └── 0001_initial.sql    # Symlink/copy from docs/SCHEMA.sql at bootstrap
│   ├── seeds/
│   │   └── dev_products.sql    # Sample catalog for local dev
│   └── README.md               # Migration workflow
│
├── deploy/
│   ├── compose/
│   │   ├── docker-compose.yml          # Full stack: api, web, worker, pg, redis
│   │   ├── docker-compose.dev.yml      # Dev overrides (hot reload, mock Razorpay)
│   │   └── docker-compose.test.yml     # CI integration test stack
│   └── docker/
│       └── nginx.conf                  # Optional reverse proxy (non-cloud)
│
├── tests/
│   └── e2e/                    # Cross-service Playwright + API flows
│       ├── playwright.config.ts
│       └── specs/
│           └── checkout-flow.spec.ts
│
├── scripts/
│   ├── dev/
│   │   ├── bootstrap.ps1       # First-time local setup (Windows)
│   │   ├── bootstrap.sh        # First-time local setup (Unix)
│   │   └── seed.sh             # Load dev catalog
│   ├── ci/
│   │   └── run-integration.sh
│   └── generate_*.py           # PDF generators (optional exports from canonical docs)
│
├── docs/                       # Product & engineering specs
│   ├── PRD.md
│   ├── ARCHITECTURE.md         # Canonical architecture (merged workflow + V4)
│   ├── API_SPEC.md
│   ├── SCHEMA.sql              # Canonical DDL + auth tables — do not fork elsewhere
│   └── ...
│
├── .env.example                # All required env vars (no secrets)
├── .gitignore
├── Makefile                    # dev, test, lint, migrate shortcuts
├── STRUCTURE.md                # This file
└── readme.md
```

---

## Module map (docs → code)

| Doc reference | Code path |
|---------------|-----------|
| `api/graph/keen_checkout.py` | `api/graph/keen_checkout.py` |
| `api/graph/state.py` | `api/graph/state.py` |
| `api/policy/engine.py` | `api/policy/engine.py` |
| `api/services/razorpay.py` | `api/services/razorpay.py` |
| `api/schemas/` | `api/schemas/` |
| `workers/webhook_processor.py` | `workers/webhook_processor.py` |
| `GET /api/v1/health` | `api/routers/health.py` |
| `WS /ws/v1/session` | `api/websockets/session.py` |

---

## Boundaries (do not cross)

| Layer | May call | Must not call |
|-------|----------|---------------|
| `routers/`, `websockets/` | `services/`, `schemas/` | `repositories/` directly, Razorpay SDK |
| `graph/nodes/` | `services/`, `policy/` | Razorpay without `assert_payment_gates()` |
| `policy/` | `repositories/` (read-only policy inputs) | LLM, Razorpay |
| `services/razorpay.py` | Razorpay HTTP, `repositories/` | LLM |
| `services/llm.py` | LLM provider | Postgres writes, Razorpay |
| `workers/` | `services/`, `repositories/` | WebSocket push (use Redis → API) |

---

## Local dev quick start

```bash
cp .env.example .env          # fill Razorpay test keys + OPENAI_API_KEY
make bootstrap                # postgres schema + seed
make dev                      # api + frontend + redis + postgres
```

---

## What is intentionally excluded (for now)

- AWS / GCP / Azure IaC (Terraform, CloudFormation, etc.)
- Kubernetes manifests (add under `deploy/k8s/` when needed)
- Secrets managers (use host env / platform secrets)
- Separate `packages/contracts` — add when frontend needs generated OpenAPI client
