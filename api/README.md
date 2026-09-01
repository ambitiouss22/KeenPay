# KeenPay API

FastAPI + LangGraph backend. See `docs/ARCHITECTURE.md` for graph topology and `docs/API_SPEC.md` for contracts.

## Run locally

```bash
cd api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn main:app --reload
```

## Layout

| Path | Role |
|------|------|
| `graph/` | LangGraph nodes, state, edges |
| `policy/` | Deterministic guardrails (no LLM) |
| `services/` | Business logic |
| `repositories/` | Postgres access |
| `routers/` | REST handlers |
| `websockets/` | Session WebSocket |
| `schemas/` | Pydantic models |

**Rule:** LLM proposes; policy approves; only then Razorpay is called.
