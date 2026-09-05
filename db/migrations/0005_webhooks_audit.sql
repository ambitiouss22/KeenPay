-- Inbound provider events, reconciliation history, and the hash-chained
-- audit ledger.
--
-- Three properties are pushed into the storage layer, so that a bug in the
-- service cannot quietly undo them:
--
--   * one event id can only be recorded once, per merchant -- a UNIQUE index,
--     which is what makes webhook deduplication a guarantee rather than a
--     convention, and what stops a redelivered "paid" event from settling an
--     order twice;
--   * an audit entry can never be updated or deleted -- triggers, not
--     application politeness. A ledger that the application can edit proves
--     nothing about the application;
--   * the chain is contiguous -- (merchant_id, seq) is UNIQUE, so a removed
--     entry leaves a hole a verifier can see even before it checks a hash.
--
-- `webhook_events` already exists from the initial schema. This migration only
-- adds what the processor needs on top of it, so an existing deployment keeps
-- every row it has.

-- --- webhook events: additive columns ---------------------------------------

ALTER TABLE webhook_events
    ADD COLUMN IF NOT EXISTS merchant_id  VARCHAR(64) NOT NULL DEFAULT 'merchant_keen',
    -- Kept verbatim, and kept forever. Months later a dispute is about what
    -- the provider actually sent, not about what our parser made of it -- and
    -- the signature can only be re-checked against the original bytes.
    ADD COLUMN IF NOT EXISTS raw_body     TEXT,
    ADD COLUMN IF NOT EXISTS verdict      VARCHAR(32),
    ADD COLUMN IF NOT EXISTS attempts     INT NOT NULL DEFAULT 0;

-- The initial schema made event_id unique globally. Scoping it to the merchant
-- is both more correct and safer: two merchants must never be able to collide
-- on, or probe for, each other's event ids.
-- The constraint first, then the index. The initial schema declared this as a
-- UNIQUE *constraint*, and Postgres refuses to drop an index that a constraint
-- still depends on -- so dropping the index first fails outright. Dropping the
-- constraint takes its backing index with it; the DROP INDEX that follows is
-- for a database where the same name was ever created as a bare index.
ALTER TABLE webhook_events DROP CONSTRAINT IF EXISTS webhook_events_event_id_unique;
DROP INDEX IF EXISTS webhook_events_event_id_unique;

CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_events_merchant_event
    ON webhook_events (merchant_id, event_id);

CREATE INDEX IF NOT EXISTS idx_webhook_events_order
    ON webhook_events (order_id, received_at DESC)
    WHERE order_id IS NOT NULL;

-- --- reconciliation runs ----------------------------------------------------

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id                VARCHAR(50) PRIMARY KEY,
    merchant_id       VARCHAR(64)  NOT NULL,
    tenant_id         UUID,
    trigger           VARCHAR(32)  NOT NULL DEFAULT 'scheduled',
    status            VARCHAR(20)  NOT NULL DEFAULT 'running',
    checked           INT          NOT NULL DEFAULT 0,
    resolved_captured INT          NOT NULL DEFAULT 0,
    resolved_failed   INT          NOT NULL DEFAULT 0,
    still_unknown     INT          NOT NULL DEFAULT 0,
    unreachable       INT          NOT NULL DEFAULT 0,
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMPTZ,

    CONSTRAINT reconciliation_status_known
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_merchant
    ON reconciliation_runs (merchant_id, started_at DESC);

-- A run that finds nothing writes no diffs, which is the point: the rows in
-- this table are exactly the things a human still has to look at.
CREATE TABLE IF NOT EXISTS reconciliation_diffs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      VARCHAR(50)  NOT NULL REFERENCES reconciliation_runs (id) ON DELETE CASCADE,
    merchant_id VARCHAR(64)  NOT NULL,
    payment_id  VARCHAR(50)  NOT NULL,
    kind        VARCHAR(64)  NOT NULL,
    local_value TEXT,
    provider_value TEXT,
    detail      TEXT,
    resolved_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_diffs_run
    ON reconciliation_diffs (run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_reconciliation_diffs_open
    ON reconciliation_diffs (merchant_id, created_at DESC)
    WHERE resolved_at IS NULL;

-- --- audit ledger (hash-chained, append-only) -------------------------------

CREATE TABLE IF NOT EXISTS audit_ledger (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id    VARCHAR(64)  NOT NULL,
    tenant_id      UUID,
    -- Position in this merchant's chain, starting at 1. UNIQUE with
    -- merchant_id below: a gap in the sequence is a removed entry, and that is
    -- detectable without recomputing a single hash.
    seq            BIGINT       NOT NULL,
    entity_type    VARCHAR(64)  NOT NULL,
    entity_id      VARCHAR(64)  NOT NULL,
    actor          VARCHAR(64)  NOT NULL,
    action         VARCHAR(128) NOT NULL,
    payload        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    correlation_id VARCHAR(128),
    -- 64 hex characters of SHA-256. prev_hash is inside the hashed body, not
    -- merely stored beside it, so entries cannot be reordered while each
    -- individual hash still checks out.
    prev_hash      CHAR(64)     NOT NULL,
    entry_hash     CHAR(64)     NOT NULL,
    recorded_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT audit_ledger_seq_positive CHECK (seq > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_ledger_chain
    ON audit_ledger (merchant_id, seq);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_ledger_entry_hash
    ON audit_ledger (merchant_id, entry_hash);

CREATE INDEX IF NOT EXISTS idx_audit_ledger_entity
    ON audit_ledger (merchant_id, entity_type, entity_id, seq);

CREATE INDEX IF NOT EXISTS idx_audit_ledger_action
    ON audit_ledger (merchant_id, action, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_ledger_correlation
    ON audit_ledger (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Append-only, enforced by the database. The application has no update or
-- delete path for this table, and this makes sure a future one cannot be added
-- by accident.
CREATE OR REPLACE FUNCTION audit_ledger_deny_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_ledger is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_ledger_no_update ON audit_ledger;
CREATE TRIGGER trg_audit_ledger_no_update
    BEFORE UPDATE ON audit_ledger
    FOR EACH ROW EXECUTE FUNCTION audit_ledger_deny_mutation();

DROP TRIGGER IF EXISTS trg_audit_ledger_no_delete ON audit_ledger;
CREATE TRIGGER trg_audit_ledger_no_delete
    BEFORE DELETE ON audit_ledger
    FOR EACH ROW EXECUTE FUNCTION audit_ledger_deny_mutation();

-- --- row-level security -----------------------------------------------------
-- Same posture as the payment tables: the tenant boundary is enforced by the
-- database, so a query that forgets to filter returns nothing rather than
-- everything.

ALTER TABLE reconciliation_runs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconciliation_diffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_ledger         ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS reconciliation_runs_tenant_isolation ON reconciliation_runs;
DROP POLICY IF EXISTS tenant_isolation ON reconciliation_runs;
CREATE POLICY tenant_isolation ON reconciliation_runs
    USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

DROP POLICY IF EXISTS reconciliation_diffs_tenant_isolation ON reconciliation_diffs;
DROP POLICY IF EXISTS tenant_isolation ON reconciliation_diffs;
CREATE POLICY tenant_isolation ON reconciliation_diffs
    USING (
        run_id IN (
            SELECT id FROM reconciliation_runs
            WHERE tenant_id IS NULL
               OR tenant_id = current_setting('app.tenant_id', TRUE)::UUID
        )
    );

DROP POLICY IF EXISTS audit_ledger_tenant_isolation ON audit_ledger;
DROP POLICY IF EXISTS tenant_isolation ON audit_ledger;
CREATE POLICY tenant_isolation ON audit_ledger
    USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', TRUE)::UUID);
