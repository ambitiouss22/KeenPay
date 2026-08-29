# Development log

Bugs, wrong turns, and fixes. Newest first. If you change anything that touches money, add a line here.

---

### 2026-08-29 — Readme schema drift

**Symptom:** Early readme showed a `merchants` + `audit_ledger` sketch that did not match `SCHEMA.sql`.  
**Root cause:** README was written before DDL settled.  
**Resolution:** Readme now points at `docs/SCHEMA.sql` only. Passport is derived, not a table.  
**Prevention:** One DDL file; no inline schema copies in markdown.  
**Files changed:** `readme.md`

---

### 2026-08-28 — Float rupees in an early API draft

**Symptom:** A prototype response used `"final_total": 4498.00` in rupees.  
**Root cause:** Copied e-commerce JSON habits; guardrail tests would not be reproducible.  
**Resolution:** All amounts are integer paise end-to-end (`final_amount_paise`). Display formatting stays in the UI.  
**Prevention:** Pydantic models use `int` for money fields; CI property test on totals.  
**Files changed:** `docs/API_SPEC.md`, `docs/ARCHITECTURE.md`

---

### 2026-08-27 — Almost exposed payment as an LLM tool

**Symptom:** First LangGraph sketch had `create_payment_link` callable from the tool router.  
**Root cause:** Default agent pattern — everything becomes a tool.  
**Resolution:** Payment is a graph node only, behind `assert_payment_gates()`. LLM tool list is catalog + propose_offer.  
**Prevention:** `AI_JUDGMENT.md` prohibited patterns; integration test `test_llm_never_calls_razorpay`.  
**Files changed:** `docs/ARCHITECTURE.md`, `docs/AI_JUDGMENT.md`

---

### 2026-08-26 — Negotiation loop without a cap

**Symptom:** User could bounce REJECTED -> negotiate forever in the state diagram.  
**Root cause:** Missing `negotiation_round` check on the retry edge.  
**Resolution:** Max 5 rounds, then `ESCALATED` -> `escalation_tickets`.  
**Prevention:** `RULE_NEGOTIATION_ROUNDS` + graph edge `after_rejection`.  
**Files changed:** `docs/ARCHITECTURE.md`, `docs/GUARDRAILS_AND_SAFETY.md`

---

### 2026-08-25 — Webhook paid the wrong amount in a spike

**Symptom:** Test webhook handler set `orders.status = paid` without comparing amount.  
**Root cause:** Happy-path only implementation.  
**Resolution:** `amount_paise` must equal `orders.final_amount_paise`; else `payment_disputed` + audit `WEBHOOK_AMOUNT_MISMATCH`.  
**Prevention:** Documented in failure matrix; handler unit test with mismatched payload.  
**Files changed:** `docs/GUARDRAILS_AND_SAFETY.md`, `docs/API_SPEC.md`

---

### 2026-08-24 — Prompt injection test case

**Symptom:** Needed a demo path for "set price to 1 rupee" without actually creating a link.  
**Root cause:** Track requires graceful failure on injection / margin violation.  
**Resolution:** Regex list in `api/policy/anomaly.py` (spec), `security_block` on critical patterns, user gets a polite clamp message.  
**Prevention:** Golden injection strings in test suite; trace shows `security.flag`.  
**Files changed:** `docs/GUARDRAILS_AND_SAFETY.md`

---

## Architecture decisions (sticky)

| When | Choice | Why not the alternative |
|------|--------|-------------------------|
| Aug 2026 | Sync Python policy engine | LLM approval is not auditable or deterministic |
| Aug 2026 | LangGraph interrupt before pay | "Sounds good" in chat is not consent |
| Aug 2026 | `audit_logs` append-only trigger | Mutable audit is useless in a dispute |
| Aug 2026 | Postgres catalog search, not RAG | Hallucinated SKUs are a fulfillment incident |
| Aug 2026 | Razorpay Payment Links | Hosted checkout; we do not touch card data |

---

## Log template

```markdown
### YYYY-MM-DD — title

**Symptom:**
**Root cause:**
**Resolution:**
**Prevention:**
**Files changed:**
```
