# Development log

Bugs, wrong turns, and fixes. Newest first. If you change anything that touches money, add a line here.

---

### 2026-09-04 — UNKNOWN payments could never be reconciled

**Symptom:** A payment whose capture timed out went to UNKNOWN with `provider_payment_id = NULL`, so the reconciliation pass had nothing to ask the provider about and the payment stayed UNKNOWN forever.
**Root cause:** The provider's id was only persisted by `mark_captured()`, which by definition does not run when the capture times out. Exactly the payments that need reconciling were the ones that could not be.
**Resolution:** `PaymentRepository.set_provider_reference()`, called immediately after `create_order()` returns and before capture is attempted.
**Prevention:** Integration test drives timeout -> UNKNOWN -> reconciliation -> CAPTURED; the reconciliation engine records a `no_provider_reference` diff rather than silently skipping.
**Files changed:** `api/repositories/payments.py`, `api/services/payments.py`, `api/modules/reconciliation/worker.py`

---

### 2026-09-04 — Webhook amount mismatch and replay hardening

**Symptom:** The handler compared the event's amount to the order inline and had no freshness check, so a captured signature replayed later was still a valid event.
**Root cause:** Verification, deduplication and application were interleaved in the route, and an event carrying no `amount` compared as "not equal" only by accident.
**Resolution:** Extracted to `modules/webhooks/processor.py` with a fixed order — verify signature over raw bytes, reject events more than 5 minutes from now, claim the event id, only then act. Amount is compared against `orders.final_amount_paise`; any mismatch, including a missing amount, sets `payment_disputed` and returns 409. A late `payment_link.expired` can no longer unpay a settled order.
**Prevention:** Replay, forged-signature, short-payment and malformed-body cases are in `tests/security/test_webhook_replay.py`.
**Files changed:** `api/modules/webhooks/processor.py`, `api/routers/webhooks.py`, `api/repositories/webhooks.py`, `api/repositories/orders.py`

---

### 2026-09-04 — Reserved log key raised from inside the logging call

**Symptom:** Every webhook that hit the duplicate, ignored or order-not-found path returned HTTP 500 with an enormous traceback.
**Root cause:** `logger.info("webhook_duplicate", event=event_type)` — structlog binds the log message itself to `event`, so passing it as a keyword is a `TypeError` raised inside the logging call, surfacing as an unhandled exception in an unrelated handler.
**Resolution:** Renamed to `event_type=`; the two `**dict` splats into loggers were made explicit named fields so a dict that later grows an `event` key cannot reintroduce it.
**Prevention:** Never splat an untrusted dict into a structlog call. An AST sweep over `api/` and `workers/` reports zero remaining cases.
**Files changed:** `api/modules/webhooks/processor.py`, `api/modules/reconciliation/worker.py`, `workers/jobs/reconciliation.py`

---

### 2026-08-30 — Architecture and schema doc consolidation

**Symptom:** Architecture and schema knowledge was split across multiple PDFs (`KeenPay_Architecture_Workflow`, `KEENPAY_Agentic_Commerce_Architecture_V4`, `KeenPay_Database_Schema`) and a separate auth migration.  
**Root cause:** Iterative doc generation during AegisPay → KeenPay rebrand and V4 protocol gateway design.  
**Resolution:** Merged into `docs/ARCHITECTURE.md` (workflow + V4 protocol gateway + implementation map) and `docs/SCHEMA.sql` (DDL + auth tables + design notes). Removed superseded PDFs and `generate_aegispay_architecture_v4_pdf.py`.  
**Prevention:** One architecture markdown, one schema SQL; PDF generators are optional exports only.  
**Files changed:** `docs/ARCHITECTURE.md`, `docs/SCHEMA.sql`, `STRUCTURE.md`, `db/README.md`, `docs/AUTH.md`, bootstrap scripts, `Makefile`

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
