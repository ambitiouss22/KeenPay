-- =============================================================================
-- KeenPay — 0001_initial: schema, tenancy, row-level security
-- =============================================================================
-- Run as keenpay_migration, AFTER db/init.sql has created the roles:
--
--   psql "postgresql://keenpay_migration:...@localhost:5432/keenpay" \
--        -v ON_ERROR_STOP=1 -f db/migrations/0001_initial.sql
--
-- Mirrors api/db/models.py. That file is the source of truth; if the two ever
-- disagree, this migration is the bug.
--
-- Safe on a fresh database and on one that already ran docs/SCHEMA.sql. Tables
-- use CREATE TABLE IF NOT EXISTS, and tenant_id is added with ADD COLUMN IF NOT
-- EXISTS then backfilled, so an existing v1 install migrates in place rather
-- than needing a drop.
--
-- docs/SCHEMA.sql is now historical. Migrations are canonical from here on.
--
-- The isolation model, in one line: every tenant-owned table carries tenant_id
-- and a policy of USING (tenant_id = current_setting('app.tenant_id')::uuid),
-- and the runtime role cannot bypass it.
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- =============================================================================
-- PRECONDITIONS
-- =============================================================================
-- pgcrypto (for gen_random_uuid) is installed by db/init.sql, which runs as a
-- superuser. keenpay_migration deliberately lacks the privilege to create
-- extensions, so this only verifies and fails loudly with the fix.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION
            'pgcrypto extension missing. Run db/init.sql as a superuser first.';
    END IF;
END
$$;

-- =============================================================================
-- ENUM TYPES
-- =============================================================================
-- CREATE TYPE has no IF NOT EXISTS, so each is guarded.

DO $$ BEGIN
    CREATE TYPE order_status AS ENUM
        ('pending', 'paid', 'expired', 'cancelled', 'payment_disputed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE negotiation_session_status AS ENUM
        ('active', 'negotiating', 'awaiting_confirmation', 'payment_pending',
         'paid', 'escalated', 'closed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE guardrail_outcome AS ENUM ('APPROVED', 'REJECTED', 'ESCALATED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE audit_actor AS ENUM
        ('agent', 'policy_engine', 'user', 'system', 'webhook', 'human');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM
        ('shopper', 'support_agent', 'manager', 'admin', 'service');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE auth_event_type AS ENUM
        ('login_success', 'login_failed', 'token_issued', 'token_refreshed',
         'token_revoked', 'api_key_used', 'password_changed', 'account_locked');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE payment_status AS ENUM
        ('created', 'authorized', 'captured', 'failed', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- =============================================================================
-- TENANTS — root of the isolation model, not itself tenant-scoped
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(255) NOT NULL,
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    settings    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE tenants IS 'One merchant organisation. slug is the legacy merchant_id value.';
COMMENT ON COLUMN tenants.slug IS 'Human-readable merchant_id, e.g. merchant_keen. Joins legacy rows to a tenant.';

-- The default tenant must exist before any backfill can reference it.
INSERT INTO tenants (slug, name)
VALUES ('merchant_keen', 'KeenPay Demo Merchant')
ON CONFLICT (slug) DO NOTHING;

-- =============================================================================
-- AUTH
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id                  VARCHAR(64) PRIMARY KEY
                        DEFAULT ('user_' || replace(gen_random_uuid()::text, '-', '')),
    merchant_id         VARCHAR(64)  NOT NULL DEFAULT 'merchant_keen',
    email               VARCHAR(255) NOT NULL,
    password_hash       VARCHAR(255),
    role                user_role    NOT NULL DEFAULT 'shopper',
    display_name        VARCHAR(255),
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    locked_until        TIMESTAMPTZ,
    failed_login_count  INTEGER      NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    last_login_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT users_email_merchant_unique UNIQUE (merchant_id, email)
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   VARCHAR(64) NOT NULL UNIQUE,
    family_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ,
    replaced_by  UUID REFERENCES refresh_tokens(id),
    user_agent   VARCHAR(512),
    ip_address   INET,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            VARCHAR(64) PRIMARY KEY
                  DEFAULT ('key_' || replace(gen_random_uuid()::text, '-', '')),
    merchant_id   VARCHAR(64)  NOT NULL DEFAULT 'merchant_keen',
    name          VARCHAR(255) NOT NULL,
    key_prefix    VARCHAR(16)  NOT NULL,
    key_hash      VARCHAR(64)  NOT NULL UNIQUE,
    role          user_role    NOT NULL DEFAULT 'service',
    scopes        TEXT[]       NOT NULL DEFAULT '{}',
    active        BOOLEAN      NOT NULL DEFAULT TRUE,
    expires_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    created_by    VARCHAR(64) REFERENCES users(id),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revoked_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS auth_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    merchant_id  VARCHAR(64),
    event_type   auth_event_type NOT NULL,
    user_id      VARCHAR(64) REFERENCES users(id),
    api_key_id   VARCHAR(64) REFERENCES api_keys(id),
    ip_address   INET,
    user_agent   VARCHAR(512),
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- CATALOG
-- =============================================================================

CREATE TABLE IF NOT EXISTS products (
    id                 VARCHAR(64) PRIMARY KEY,
    merchant_id        VARCHAR(64)  NOT NULL DEFAULT 'merchant_keen',
    sku                VARCHAR(64)  NOT NULL,
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    list_price_paise   INTEGER      NOT NULL CHECK (list_price_paise >= 0),
    cost_paise         INTEGER      NOT NULL CHECK (cost_paise >= 0),
    wholesale_paise    INTEGER CHECK (wholesale_paise IS NULL OR wholesale_paise >= 0),
    quantity_on_hand   INTEGER      NOT NULL DEFAULT 0 CHECK (quantity_on_hand >= 0),
    quantity_reserved  INTEGER      NOT NULL DEFAULT 0 CHECK (quantity_reserved >= 0),
    attributes         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    search_vector      TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(name, '') || ' ' || coalesce(description, '') || ' ' ||
            coalesce(sku, '')  || ' ' || coalesce(attributes::text, ''))
    ) STORED,
    active             BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT products_sku_merchant_unique UNIQUE (merchant_id, sku),
    CONSTRAINT products_reserved_lte_on_hand CHECK (quantity_reserved <= quantity_on_hand)
);

CREATE INDEX IF NOT EXISTS idx_products_sku ON products (sku);
CREATE INDEX IF NOT EXISTS idx_products_search ON products USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_products_attributes ON products USING GIN (attributes jsonb_path_ops);

-- =============================================================================
-- AGENTIC CHECKOUT
-- =============================================================================

CREATE TABLE IF NOT EXISTS negotiation_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id             VARCHAR(64) NOT NULL DEFAULT 'merchant_keen',
    user_id                 VARCHAR(64),
    status                  negotiation_session_status NOT NULL DEFAULT 'active',
    negotiation_round       INTEGER NOT NULL DEFAULT 0 CHECK (negotiation_round >= 0),
    offer_version           INTEGER NOT NULL DEFAULT 0 CHECK (offer_version >= 0),
    parsed_intent           JSONB,
    search_results          JSONB   NOT NULL DEFAULT '[]'::jsonb,
    selected_line_items     JSONB   NOT NULL DEFAULT '[]'::jsonb,
    proposed_offer          JSONB,
    approved_offer          JSONB,
    guardrail_decision      guardrail_outcome,
    guardrail_decision_id   UUID,
    guardrail_detail        JSONB,
    rejection_reasons       JSONB   NOT NULL DEFAULT '[]'::jsonb,
    user_confirmed_payment  BOOLEAN NOT NULL DEFAULT FALSE,
    user_confirmed_at       TIMESTAMPTZ,
    final_amount_paise      INTEGER CHECK (final_amount_paise IS NULL OR final_amount_paise > 0),
    currency                CHAR(3) NOT NULL DEFAULT 'INR',
    anomaly_flags           JSONB   NOT NULL DEFAULT '[]'::jsonb,
    security_block          BOOLEAN NOT NULL DEFAULT FALSE,
    langgraph_thread_id     UUID    NOT NULL,
    metadata                JSONB   NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at               TIMESTAMPTZ,
    CONSTRAINT negotiation_sessions_currency_inr CHECK (currency = 'INR')
);

CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_user
    ON negotiation_sessions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_status
    ON negotiation_sessions (status) WHERE status NOT IN ('closed', 'paid');

CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
    thread_id      UUID         NOT NULL,
    checkpoint_ns  VARCHAR(256) NOT NULL DEFAULT '',
    checkpoint_id  UUID         NOT NULL,
    parent_id      UUID,
    checkpoint     JSONB        NOT NULL,
    metadata       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- =============================================================================
-- COMMERCE
-- =============================================================================

CREATE TABLE IF NOT EXISTS orders (
    id                         VARCHAR(64) PRIMARY KEY,
    merchant_id                VARCHAR(64) NOT NULL DEFAULT 'merchant_keen',
    session_id                 UUID NOT NULL
                               REFERENCES negotiation_sessions(id) ON DELETE RESTRICT,
    user_id                    VARCHAR(64),
    status                     order_status NOT NULL DEFAULT 'pending',
    subtotal_paise             INTEGER NOT NULL CHECK (subtotal_paise >= 0),
    discount_amount_paise      INTEGER NOT NULL DEFAULT 0 CHECK (discount_amount_paise >= 0),
    final_amount_paise         INTEGER NOT NULL CHECK (final_amount_paise > 0),
    currency                   CHAR(3) NOT NULL DEFAULT 'INR',
    line_items                 JSONB   NOT NULL,
    guardrail_decision_id      UUID    NOT NULL,
    offer_version              INTEGER NOT NULL CHECK (offer_version >= 1),
    policy_version             VARCHAR(32) NOT NULL,
    razorpay_payment_link_id   VARCHAR(64),
    razorpay_payment_link_url  TEXT,
    razorpay_payment_id        VARCHAR(64),
    razorpay_order_id          VARCHAR(64),
    payment_link_expires_at    TIMESTAMPTZ,
    idempotency_key            VARCHAR(128) NOT NULL,
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    paid_at                    TIMESTAMPTZ,
    expired_at                 TIMESTAMPTZ,
    cancelled_at               TIMESTAMPTZ,
    CONSTRAINT orders_currency_inr CHECK (currency = 'INR'),
    CONSTRAINT orders_idempotency_unique UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders (session_id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_razorpay_link
    ON orders (razorpay_payment_link_id) WHERE razorpay_payment_link_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_razorpay_payment
    ON orders (razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_one_pending_per_session
    ON orders (session_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS order_items (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id          VARCHAR(64) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id        VARCHAR(64) REFERENCES products(id),
    sku               VARCHAR(64)  NOT NULL,
    name              VARCHAR(255) NOT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_paise  INTEGER NOT NULL CHECK (unit_price_paise >= 0),
    line_total_paise  INTEGER NOT NULL CHECK (line_total_paise >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);

CREATE TABLE IF NOT EXISTS carts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID REFERENCES negotiation_sessions(id) ON DELETE CASCADE,
    user_id     VARCHAR(64),
    status      VARCHAR(16) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'converted', 'abandoned')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cart_items (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id           UUID NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    product_id        VARCHAR(64) REFERENCES products(id),
    sku               VARCHAR(64) NOT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_paise  INTEGER NOT NULL CHECK (unit_price_paise >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT cart_items_cart_sku_unique UNIQUE (cart_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items (cart_id);

CREATE TABLE IF NOT EXISTS inventory_holds (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES negotiation_sessions(id) ON DELETE CASCADE,
    product_id   VARCHAR(64) NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    sku          VARCHAR(64) NOT NULL,
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    expires_at   TIMESTAMPTZ NOT NULL,
    released_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_holds_active_unique UNIQUE (session_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_inventory_holds_expires
    ON inventory_holds (expires_at) WHERE released_at IS NULL;

-- =============================================================================
-- PAYMENTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS payments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id             VARCHAR(64) NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    status               payment_status NOT NULL DEFAULT 'created',
    amount_paise         BIGINT      NOT NULL CHECK (amount_paise > 0),
    currency             CHAR(3)     NOT NULL DEFAULT 'INR',
    provider             VARCHAR(32) NOT NULL DEFAULT 'razorpay',
    provider_payment_id  VARCHAR(64),
    provider_order_id    VARCHAR(64),
    method               VARCHAR(32),
    idempotency_key      VARCHAR(128) NOT NULL,
    captured_at          TIMESTAMPTZ,
    failure_reason       TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT payments_idempotency_unique UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments (order_id);

CREATE TABLE IF NOT EXISTS payment_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        VARCHAR(64) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    payment_id      UUID REFERENCES payments(id) ON DELETE SET NULL,
    attempt_number  INTEGER     NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
    status          VARCHAR(32) NOT NULL,
    amount_paise    BIGINT      NOT NULL,
    error_code      VARCHAR(64),
    error_detail    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_attempts_order ON payment_attempts (order_id);
CREATE INDEX IF NOT EXISTS idx_payment_attempts_payment ON payment_attempts (payment_id);

CREATE TABLE IF NOT EXISTS authorizations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID REFERENCES negotiation_sessions(id) ON DELETE CASCADE,
    user_id                VARCHAR(64),
    scope                  VARCHAR(64) NOT NULL,
    max_amount_paise       BIGINT NOT NULL CHECK (max_amount_paise > 0),
    consumed_amount_paise  BIGINT NOT NULL DEFAULT 0 CHECK (consumed_amount_paise >= 0),
    granted_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at             TIMESTAMPTZ NOT NULL,
    revoked_at             TIMESTAMPTZ,
    constraints            JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT authorizations_not_overspent
        CHECK (consumed_amount_paise <= max_amount_paise)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         VARCHAR(128) NOT NULL,
    event_type       VARCHAR(64)  NOT NULL,
    payload          JSONB        NOT NULL,
    signature_valid  BOOLEAN      NOT NULL,
    processed        BOOLEAN      NOT NULL DEFAULT FALSE,
    process_result   JSONB,
    order_id         VARCHAR(64) REFERENCES orders(id) ON DELETE SET NULL,
    received_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    processed_at     TIMESTAMPTZ,
    CONSTRAINT webhook_events_event_id_unique UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_unprocessed
    ON webhook_events (received_at) WHERE processed = FALSE;

CREATE TABLE IF NOT EXISTS reconciliation (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id               VARCHAR(64) REFERENCES orders(id) ON DELETE SET NULL,
    payment_id             UUID REFERENCES payments(id) ON DELETE SET NULL,
    provider_reference     VARCHAR(128),
    expected_amount_paise  BIGINT,
    actual_amount_paise    BIGINT,
    status                 VARCHAR(32) NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'matched', 'discrepant', 'resolved')),
    discrepancy            JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_order ON reconciliation (order_id);
CREATE INDEX IF NOT EXISTS idx_reconciliation_payment ON reconciliation (payment_id);

-- =============================================================================
-- GROWTH
-- =============================================================================

CREATE TABLE IF NOT EXISTS campaigns (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              VARCHAR(255) NOT NULL,
    code              VARCHAR(64),
    budget_paise      BIGINT NOT NULL CHECK (budget_paise >= 0),
    reserved_paise    BIGINT NOT NULL DEFAULT 0 CHECK (reserved_paise >= 0),
    spent_paise       BIGINT NOT NULL DEFAULT 0 CHECK (spent_paise >= 0),
    max_discount_pct  NUMERIC(5, 2),
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    starts_at         TIMESTAMPTZ,
    ends_at           TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The database, not the application, is what makes overspend impossible.
    CONSTRAINT campaigns_budget_not_exceeded
        CHECK (reserved_paise + spent_paise <= budget_paise)
);

CREATE TABLE IF NOT EXISTS budget_ledger (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id          UUID NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
    order_id             VARCHAR(64) REFERENCES orders(id) ON DELETE SET NULL,
    entry_type           VARCHAR(16) NOT NULL
                         CHECK (entry_type IN ('reserve', 'release', 'spend', 'refund')),
    amount_paise         BIGINT NOT NULL,
    balance_after_paise  BIGINT,
    reason               TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budget_ledger_campaign ON budget_ledger (campaign_id);

CREATE TABLE IF NOT EXISTS opportunities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID REFERENCES negotiation_sessions(id) ON DELETE CASCADE,
    user_id     VARCHAR(64),
    kind        VARCHAR(64) NOT NULL,
    score       NUMERIC(5, 4),
    payload     JSONB   NOT NULL DEFAULT '{}'::jsonb,
    acted_on    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- AUDIT AND INTEGRITY
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id      VARCHAR(64) NOT NULL DEFAULT 'merchant_keen',
    session_id       UUID REFERENCES negotiation_sessions(id) ON DELETE SET NULL,
    order_id         VARCHAR(64) REFERENCES orders(id) ON DELETE SET NULL,
    actor            audit_actor  NOT NULL,
    action           VARCHAR(128) NOT NULL,
    decision_id      UUID,
    offer_version    INTEGER,
    idempotency_key  VARCHAR(128),
    trace_event_id   UUID,
    input_snapshot   JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address       INET,
    user_agent       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_session_created
    ON audit_logs (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_decision
    ON audit_logs (decision_id) WHERE decision_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS passport_records (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id         VARCHAR(64) REFERENCES orders(id) ON DELETE SET NULL,
    session_id       UUID REFERENCES negotiation_sessions(id) ON DELETE SET NULL,
    sequence_number  BIGINT      NOT NULL,
    event_type       VARCHAR(64) NOT NULL,
    payload          JSONB       NOT NULL,
    prev_hash        VARCHAR(64),
    record_hash      VARCHAR(64) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_passport_records_order ON passport_records (order_id);

CREATE TABLE IF NOT EXISTS escalation_tickets (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id               UUID NOT NULL
                             REFERENCES negotiation_sessions(id) ON DELETE CASCADE,
    priority                 VARCHAR(4)  NOT NULL CHECK (priority IN ('P0', 'P1', 'P2')),
    reason_code              VARCHAR(64) NOT NULL,
    status                   VARCHAR(16) NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'assigned', 'resolved', 'expired')),
    assigned_to              VARCHAR(64),
    proposed_offer_snapshot  JSONB NOT NULL,
    policy_snapshot          JSONB NOT NULL,
    resolution               VARCHAR(32)
                             CHECK (resolution IN ('approve_override', 'deny', 'counter_offer')),
    override_discount_pct    NUMERIC(5, 2),
    resolver_note            TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_escalation_tickets_status
    ON escalation_tickets (status, priority, created_at);

-- =============================================================================
-- INFRASTRUCTURE
-- =============================================================================

CREATE TABLE IF NOT EXISTS outbox (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type  VARCHAR(64) NOT NULL,
    aggregate_id    VARCHAR(64) NOT NULL,
    event_type      VARCHAR(64) NOT NULL,
    payload         JSONB       NOT NULL,
    published_at    TIMESTAMPTZ,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished
    ON outbox (created_at) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key              VARCHAR(128) NOT NULL,
    endpoint         VARCHAR(128) NOT NULL,
    request_hash     VARCHAR(64),
    response_status  INTEGER,
    response_body    JSONB,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires ON idempotency_keys (expires_at);

CREATE TABLE IF NOT EXISTS events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type    VARCHAR(64) NOT NULL,
    subject_type  VARCHAR(64),
    subject_id    VARCHAR(64),
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TENANT COLUMN — add, backfill, constrain
-- =============================================================================
-- Written as a loop so the 26 tenant tables cannot drift apart by a typo. On a
-- fresh database the UPDATE is a no-op; on an existing v1 install it maps every
-- historical row onto the tenant its merchant_id already named.

DO $$
DECLARE
    t               TEXT;
    default_tenant  UUID;
    tenant_tables   TEXT[] := ARRAY[
        'users', 'refresh_tokens', 'api_keys', 'auth_audit_log',
        'products', 'negotiation_sessions', 'langgraph_checkpoints',
        'orders', 'order_items', 'carts', 'cart_items', 'inventory_holds',
        'payments', 'payment_attempts', 'authorizations', 'webhook_events',
        'reconciliation', 'campaigns', 'budget_ledger', 'opportunities',
        'audit_logs', 'passport_records', 'escalation_tickets',
        'outbox', 'idempotency_keys', 'events'
    ];
BEGIN
    SELECT id INTO STRICT default_tenant FROM tenants WHERE slug = 'merchant_keen';

    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS tenant_id UUID', t);

        -- Backfill. Tables carrying merchant_id map through the slug; the rest
        -- inherit the default tenant.
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = t AND column_name = 'merchant_id'
        ) THEN
            EXECUTE format(
                'UPDATE %I tbl SET tenant_id = COALESCE('
                '   (SELECT tn.id FROM tenants tn WHERE tn.slug = tbl.merchant_id), %L)'
                ' WHERE tbl.tenant_id IS NULL', t, default_tenant);
        ELSE
            EXECUTE format('UPDATE %I SET tenant_id = %L WHERE tenant_id IS NULL',
                           t, default_tenant);
        END IF;

        EXECUTE format('ALTER TABLE %I ALTER COLUMN tenant_id SET NOT NULL', t);

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = t || '_tenant_id_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (tenant_id)'
                ' REFERENCES tenants(id) ON DELETE RESTRICT',
                t, t || '_tenant_id_fkey');
        END IF;

        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (tenant_id)',
                       'idx_' || t || '_tenant_id', t);
    END LOOP;
END
$$;

-- Composite indexes on the hot tenant-scoped read paths.
CREATE INDEX IF NOT EXISTS idx_products_tenant_active ON products (tenant_id, active);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_negotiation_sessions_tenant_created
    ON negotiation_sessions (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created
    ON audit_logs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_tenant_role ON users (tenant_id, role);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_active ON campaigns (tenant_id, active);
CREATE INDEX IF NOT EXISTS idx_events_tenant_type_created
    ON events (tenant_id, event_type, created_at DESC);

-- Uniqueness that must hold per tenant rather than globally.
CREATE UNIQUE INDEX IF NOT EXISTS idempotency_keys_tenant_key_unique
    ON idempotency_keys (tenant_id, key);
CREATE UNIQUE INDEX IF NOT EXISTS passport_records_tenant_seq_unique
    ON passport_records (tenant_id, sequence_number);

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================
-- The policy reads the tenant from a session GUC that the application sets per
-- transaction. NULLIF handles the unset case: current_setting(..., true)
-- returns NULL when the GUC was never set and '' in some pooled-connection
-- paths, and casting '' to uuid would raise. Both collapse to NULL, the
-- comparison yields NULL, and the row is not visible.
--
-- Fail-closed is the point. A query that forgets to pin a tenant returns zero
-- rows, never someone else's.

DO $$
DECLARE
    t             TEXT;
    tenant_tables TEXT[] := ARRAY[
        'users', 'refresh_tokens', 'api_keys', 'auth_audit_log',
        'products', 'negotiation_sessions', 'langgraph_checkpoints',
        'orders', 'order_items', 'carts', 'cart_items', 'inventory_holds',
        'payments', 'payment_attempts', 'authorizations', 'webhook_events',
        'reconciliation', 'campaigns', 'budget_ledger', 'opportunities',
        'audit_logs', 'passport_records', 'escalation_tickets',
        'outbox', 'idempotency_keys', 'events'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I'
            '  USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)'
            '  WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
            t);
    END LOOP;
END
$$;

-- =============================================================================
-- TRIGGERS
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t          TEXT;
    touchables TEXT[] := ARRAY['tenants', 'users', 'products',
                               'negotiation_sessions', 'orders', 'carts',
                               'payments', 'campaigns'];
BEGIN
    FOREACH t IN ARRAY touchables LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated_at ON %I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
    END LOOP;
END
$$;

-- Append-only enforcement. The ledger tables are evidence; if they can be
-- edited after the fact they are not evidence.

CREATE OR REPLACE FUNCTION deny_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t           TEXT;
    append_only TEXT[] := ARRAY['audit_logs', 'auth_audit_log',
                                'passport_records', 'budget_ledger'];
BEGIN
    FOREACH t IN ARRAY append_only LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_append_only ON %I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_append_only BEFORE UPDATE OR DELETE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION deny_mutation()', t, t);
    END LOOP;
END
$$;

-- =============================================================================
-- GRANTS
-- =============================================================================
-- keenpay_app gets DML on everything and DDL on nothing. Combined with
-- NOBYPASSRLS in db/init.sql, that is the entire runtime privilege surface.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keenpay_app') THEN
        GRANT USAGE ON SCHEMA public TO keenpay_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO keenpay_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO keenpay_app;
    END IF;
END
$$;

COMMIT;

-- =============================================================================
-- POST-MIGRATION VERIFICATION
-- =============================================================================
-- Both queries must return zero rows. Run them in CI after applying.
--
-- 1. Tenant tables missing row-level security:
--
--    SELECT c.relname
--      FROM pg_class c
--      JOIN pg_namespace n ON n.oid = c.relnamespace
--     WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
--       AND EXISTS (SELECT 1 FROM information_schema.columns
--                    WHERE table_name = c.relname AND column_name = 'tenant_id');
--
-- 2. Tenant tables missing the isolation policy:
--
--    SELECT c.relname
--      FROM pg_class c
--      JOIN pg_namespace n ON n.oid = c.relnamespace
--     WHERE n.nspname = 'public' AND c.relrowsecurity
--       AND NOT EXISTS (SELECT 1 FROM pg_policies p
--                        WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation');
-- =============================================================================
