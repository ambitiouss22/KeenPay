# KeenPay — Development Log

**Purpose:** Running record of bugs, architectural dead-ends, and resolutions encountered during KeenPay development.  
**Format:** Newest entries first.

---

## How to Log an Entry

```markdown
### YYYY-MM-DD — Short title

**Symptom:** What broke or behaved unexpectedly  
**Root cause:** Why it happened  
**Resolution:** Exact fix applied  
**Prevention:** Test, guardrail, or doc update to avoid recurrence  
**Files changed:** `path/to/file.py`
```

---

## Entries

### 2026-08-29 — Documentation baseline established

**Symptom:** No single source of truth for guardrail behavior or AI/LLM boundaries.  
**Root cause:** Greenfield project; requirements spread across conversations.  
**Resolution:** Authored `PRD.md`, `ARCHITECTURE.md`, `GUARDRAILS_AND_SAFETY.md`, `AI_JUDGMENT.md`, `API_SPEC.md`, `SCHEMA.sql`.  
**Prevention:** All money-action changes require updating guardrail docs and adding a test.  
**Files changed:** `docs/*`

---

### Template — Payment link created without APPROVED guardrail

**Symptom:** _(fill when encountered)_  
**Root cause:** _(fill when encountered)_  
**Resolution:** _(fill when encountered)_  
**Prevention:** Add integration test `test_payment_requires_approved_guardrail`.  
**Files changed:** _(fill when encountered)_

---

### Template — Webhook amount mismatch

**Symptom:** Order marked paid with wrong amount  
**Root cause:** _(fill when encountered)_  
**Resolution:** _(fill when encountered)_  
**Prevention:** `WEBHOOK_AMOUNT_MISMATCH` handler + `payment_disputed` status.  
**Files changed:** _(fill when encountered)_

---

### Template — LLM timeout mid-negotiation

**Symptom:** User sees hung chat; offer state corrupted  
**Root cause:** _(fill when encountered)_  
**Resolution:** _(fill when encountered)_  
**Prevention:** 10s asyncio timeout; no state mutation on timeout; `LLM_TIMEOUT` audit action.  
**Files changed:** _(fill when encountered)_

---

### Template — Redis unavailable during inventory hold

**Symptom:** _(fill when encountered)_  
**Root cause:** _(fill when encountered)_  
**Resolution:** _(fill when encountered)_  
**Prevention:** Fall back to PostgreSQL `FOR UPDATE`; degradation level 2 in health endpoint.  
**Files changed:** _(fill when encountered)_

---

## Architectural Decisions Log

| Date | Decision | Rationale | Alternatives rejected |
|------|----------|-----------|----------------------|
| 2026-08-29 | Policy engine is synchronous Python | Deterministic, unit-testable, no LLM in approve path | LLM-based approval, prompt-only guardrails |
| 2026-08-29 | LangGraph interrupt before payment | Explicit user consent gate | Auto-confirm from LLM intent |
| 2026-08-29 | Append-only audit_logs | Tamper-evident compliance trail | Mutable audit with corrections |
| 2026-08-29 | Integer paise everywhere | No float rounding errors in INR | Float rupees in API |
