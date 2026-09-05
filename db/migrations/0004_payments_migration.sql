-- Payments, provider attempts, idempotency keys and the outbox.
--
-- Three things this migration is written to make impossible at the storage
-- layer, so a bug in the service cannot quietly undo them:
--
--   * a refund larger than what was captured, or a capture larger than the
--     amount authorised -- CHECK constraints, not application politeness;
--   * two payments sharing one idempotency key inside a merchant and endpoint
--     -- a UNIQUE index, which is what makes "claim first" a claim rather than
--     a hope;
--   * a payment whose snapshot hash was never recorded -- an unbound payment is
--     a signed blank cheque.
--
-- ADDITIVE, and that is the correction this file exists to make. `payments`,
-- `payment_attempts` and `idempotency_keys` are all created by the initial
-- schema. An earlier version of this migration redeclared them with
-- CREATE TABLE IF NOT EXISTS, which Postgres skips in silence -- so none of the
-- columns below were ever added, and the first index that mentioned one failed
-- with "column merchant_id does not exist". Nothing noticed, because the
-- migration chain had never been run against a real database.
--
-- `merchant_id` is VARCHAR(64) holding the tenant slug, matching every other
-- table and the value the application actually passes. The earlier version
-- typed it UUID REFERENCES merchants (id) -- a table that does not exist in
-- this schema, in any migration, or anywhere in the repository.
--
-- Postgres refuses ALTER TYPE ... ADD VALUE inside the same transaction that
-- then uses the new value, so the enum values go first, on their own, and this
-- file is deliberately not wrapped in BEGIN/COMMIT.

-- --- enum values ------------------------------------------------------------

ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'auth_required';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'authorized';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'unknown';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'partially_refunded';

-- --- payments: additive columns ---------------------------------------------

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS merchant_id         VARCHAR(64) NOT NULL
                                                 DEFAULT 'merchant_keen',
    ADD COLUMN IF NOT EXISTS authorization_id    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS captured_paise      BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refunded_paise      BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS provider_raw_status VARCHAR(50),
    -- The goods this payment was authorised for, and their fingerprint. Months
    -- later a dispute is about what was actually agreed, and the only honest
    -- answer is the snapshot taken at the time.
    ADD COLUMN IF NOT EXISTS order_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS order_snapshot_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS unknown_since       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_by          VARCHAR(50);

-- NOT NULL only when every existing row can satisfy it. On a fresh database the
-- table is empty and the constraint applies immediately, which is the case that
-- matters; a deployment carrying pre-snapshot rows keeps them and is told so,
-- rather than having the whole migration fail at 3am.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM payments WHERE order_snapshot_hash IS NULL) THEN
        ALTER TABLE payments ALTER COLUMN order_snapshot_hash SET NOT NULL;
    ELSE
        RAISE NOTICE
            'payments.order_snapshot_hash left nullable: % row(s) predate it',
            (SELECT count(*) FROM payments WHERE order_snapshot_hash IS NULL);
    END IF;
END
$$;

-- Money constraints. Added through a guard because ADD CONSTRAINT has no
-- IF NOT EXISTS, and a migration that cannot be re-run is a migration that
-- fails the second time someone bootstraps a database.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'payments_capture_non_negative'
    ) THEN
        ALTER TABLE payments
            ADD CONSTRAINT payments_capture_non_negative  CHECK (captured_paise >= 0),
            ADD CONSTRAINT payments_refund_non_negative   CHECK (refunded_paise >= 0),
            ADD CONSTRAINT payments_capture_within_amount CHECK (captured_paise <= amount_paise),
            ADD CONSTRAINT payments_refund_within_capture CHECK (refunded_paise <= captured_paise);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_payments_merchant_order
    ON payments (merchant_id, order_id);

-- The reconciliation worklist. Partial, because the interesting rows are the
-- few stuck in UNKNOWN, not the many that settled.
CREATE INDEX IF NOT EXISTS idx_payments_unknown
    ON payments (status, unknown_since)
    WHERE status = 'unknown';

CREATE INDEX IF NOT EXISTS idx_payments_provider_payment_id
    ON payments (provider_payment_id)
    WHERE provider_payment_id IS NOT NULL;

-- --- provider attempts: additive columns ------------------------------------

ALTER TABLE payment_attempts
    ADD COLUMN IF NOT EXISTS merchant_id         VARCHAR(64) NOT NULL
                                                 DEFAULT 'merchant_keen',
    ADD COLUMN IF NOT EXISTS operation           VARCHAR(50),
    ADD COLUMN IF NOT EXISTS provider_payment_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS provider_raw_status VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_payment_attempts_payment
    ON payment_attempts (payment_id, created_at);

-- --- idempotency keys: additive columns -------------------------------------

ALTER TABLE idempotency_keys
    ADD COLUMN IF NOT EXISTS merchant_id  VARCHAR(64) NOT NULL
                                          DEFAULT 'merchant_keen',
    ADD COLUMN IF NOT EXISTS state        VARCHAR(20),
    ADD COLUMN IF NOT EXISTS fingerprint  VARCHAR(64),
    ADD COLUMN IF NOT EXISTS status_code  INT,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'idempotency_state_known'
    ) THEN
        -- NULL is tolerated for rows written before the claim-first state
        -- machine existed; anything else must be one of the two real states.
        ALTER TABLE idempotency_keys
            ADD CONSTRAINT idempotency_state_known
            CHECK (state IS NULL OR state IN ('in_progress', 'completed'));
    END IF;
END
$$;

-- This index IS the idempotency guarantee. Without it, two concurrent requests
-- both "claim" the key and both reach the provider.
CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_scope
    ON idempotency_keys (merchant_id, endpoint, key);

CREATE INDEX IF NOT EXISTS idx_idempotency_expiry
    ON idempotency_keys (expires_at);

-- --- outbox -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payment_outbox (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id  VARCHAR(64)  NOT NULL DEFAULT 'merchant_keen',
    tenant_id    UUID         REFERENCES tenants (id),
    aggregate_id VARCHAR(50)  NOT NULL,
    event_type   VARCHAR(100) NOT NULL,
    payload      JSONB        NOT NULL,
    published    BOOLEAN      NOT NULL DEFAULT FALSE,
    attempts     INT          NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished
    ON payment_outbox (created_at)
    WHERE published = FALSE;

-- --- row-level security -----------------------------------------------------
-- The tenant boundary is enforced by the database, so a query that forgets to
-- filter returns nothing rather than everything.
--
-- The policy is named `tenant_isolation` on every table, without a per-table
-- prefix. That is the convention the initial schema established and the one the
-- isolation suite checks for: it asserts that every table with RLS switched on
-- has a policy by that exact name, which is how a table that was secured but
-- never given a policy gets noticed. A prefixed name reads as compliant and
-- fails that check.
--
-- Dropped first because CREATE POLICY has no IF NOT EXISTS, and a migration that
-- cannot be re-run is one that fails the second time somebody bootstraps a
-- database. The prefixed names are dropped too, so a database carrying the
-- earlier spelling ends up with exactly one policy rather than two.

ALTER TABLE payments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_attempts  ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys  ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_outbox    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS payments_tenant_isolation ON payments;
DROP POLICY IF EXISTS tenant_isolation ON payments;
CREATE POLICY tenant_isolation ON payments
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

DROP POLICY IF EXISTS payment_attempts_tenant_isolation ON payment_attempts;
DROP POLICY IF EXISTS tenant_isolation ON payment_attempts;
CREATE POLICY tenant_isolation ON payment_attempts
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

DROP POLICY IF EXISTS idempotency_keys_tenant_isolation ON idempotency_keys;
DROP POLICY IF EXISTS tenant_isolation ON idempotency_keys;
CREATE POLICY tenant_isolation ON idempotency_keys
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

DROP POLICY IF EXISTS payment_outbox_tenant_isolation ON payment_outbox;
DROP POLICY IF EXISTS tenant_isolation ON payment_outbox;
CREATE POLICY tenant_isolation ON payment_outbox
    USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', TRUE)::UUID);
