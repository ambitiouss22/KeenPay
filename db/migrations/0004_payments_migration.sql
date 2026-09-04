-- Phase 6: payments, provider attempts, idempotency keys and the outbox.
--
-- Three things this migration is written to make impossible at the storage
-- layer, so a bug in the service cannot quietly undo them:
--
--   * a refund larger than what was captured, or a capture larger than the
--     amount authorised -- CHECK constraints, not application politeness;
--   * two payments sharing one idempotency key inside a merchant and endpoint
--     -- a UNIQUE index, which is what makes "claim first" a claim rather than
--     a hope;
--   * a payment whose snapshot hash was never recorded -- the column is NOT
--     NULL, because an unbound payment is a signed blank cheque.
--
-- Postgres refuses ALTER TYPE ... ADD VALUE inside the same transaction that
-- then uses the new value, so the enum values go first, on their own.

-- --- enum values ------------------------------------------------------------

ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'auth_required';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'authorized';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'unknown';
ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'partially_refunded';

-- --- payments ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payments (
    id                  VARCHAR(50) PRIMARY KEY,
    merchant_id         UUID        NOT NULL REFERENCES merchants (id),
    tenant_id           UUID        NOT NULL REFERENCES tenants (id),
    order_id            VARCHAR(50) NOT NULL REFERENCES orders (id),
    authorization_id    VARCHAR(50),
    amount_paise        BIGINT      NOT NULL,
    captured_paise      BIGINT      NOT NULL DEFAULT 0,
    refunded_paise      BIGINT      NOT NULL DEFAULT 0,
    status              payment_status NOT NULL DEFAULT 'created',
    provider_payment_id VARCHAR(100),
    provider_order_id   VARCHAR(100),
    provider_raw_status VARCHAR(50),
    -- The goods this payment was authorised for, and their fingerprint. The
    -- hash is NOT NULL: a payment with nothing to compare against could be
    -- presented for any cart.
    order_snapshot      JSONB       NOT NULL,
    order_snapshot_hash VARCHAR(64) NOT NULL,
    idempotency_key     VARCHAR(100) NOT NULL,
    unknown_since       TIMESTAMPTZ,
    created_by          VARCHAR(50),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT payments_amount_positive       CHECK (amount_paise > 0),
    CONSTRAINT payments_capture_non_negative  CHECK (captured_paise >= 0),
    CONSTRAINT payments_refund_non_negative   CHECK (refunded_paise >= 0),
    CONSTRAINT payments_capture_within_amount CHECK (captured_paise <= amount_paise),
    CONSTRAINT payments_refund_within_capture CHECK (refunded_paise <= captured_paise)
);

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

-- --- provider attempts (append-only) ----------------------------------------

CREATE TABLE IF NOT EXISTS payment_attempts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id         UUID        NOT NULL REFERENCES merchants (id),
    tenant_id           UUID        NOT NULL REFERENCES tenants (id),
    payment_id          VARCHAR(50) NOT NULL REFERENCES payments (id),
    operation           VARCHAR(50) NOT NULL,
    provider_payment_id VARCHAR(100),
    provider_raw_status VARCHAR(50),
    error_code          VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_attempts_payment
    ON payment_attempts (payment_id, created_at);

-- --- idempotency keys -------------------------------------------------------

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id   UUID         NOT NULL REFERENCES merchants (id),
    tenant_id     UUID         NOT NULL REFERENCES tenants (id),
    endpoint      VARCHAR(100) NOT NULL,
    key           VARCHAR(100) NOT NULL,
    state         VARCHAR(20)  NOT NULL,
    fingerprint   VARCHAR(64)  NOT NULL,
    status_code   INT,
    response_body JSONB,
    completed_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ  NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT idempotency_state_known CHECK (state IN ('in_progress', 'completed'))
);

-- This index IS the idempotency guarantee. Without it, two concurrent requests
-- both "claim" the key and both reach the provider.
CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_scope
    ON idempotency_keys (merchant_id, endpoint, key);

CREATE INDEX IF NOT EXISTS idx_idempotency_expiry
    ON idempotency_keys (expires_at);

-- --- outbox -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS payment_outbox (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id  UUID         REFERENCES merchants (id),
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
-- Same posture as phase 3: the tenant boundary is enforced by the database, so
-- a query that forgets to filter returns nothing rather than everything.

ALTER TABLE payments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_attempts  ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys  ENABLE ROW LEVEL SECURITY;

CREATE POLICY payments_tenant_isolation ON payments
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

CREATE POLICY payment_attempts_tenant_isolation ON payment_attempts
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);

CREATE POLICY idempotency_keys_tenant_isolation ON idempotency_keys
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::UUID);
