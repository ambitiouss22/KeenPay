-- =============================================================================
-- 0003 - Policy, risk and authorization (phase 5)
-- =============================================================================
-- Apply with: psql $DATABASE_URL -f db/migrations/0003_policy_authorizations.sql
--
-- Idempotent throughout: every statement is IF NOT EXISTS or guarded in a DO
-- block. A migration that only works on a database it has never touched is a
-- migration you cannot re-run after a partial failure, which is exactly when
-- you need to.
--
-- WHY THIS EXTENDS `authorizations` RATHER THAN ADDING A TABLE
--
-- The table already exists from 0001, holding the scoped spend-cap form of an
-- authorization (session, max amount, consumed amount). Phase 5 adds the
-- approval-workflow form on top: a policy decision, a risk score, a required
-- number of approvals, and the people who gave them. They are the same object
-- at different stages of its life - "permission to move this money" - and
-- splitting them across two tables would mean two places to look when
-- answering the only question that matters during an incident: was this
-- payment authorized, and by whom?
--
-- The 0001 columns are relaxed to nullable rather than dropped. Existing rows
-- keep their meaning, and the two shapes coexist while phase 6 migrates the
-- payment path over.

BEGIN;

-- --- authorizations: the phase 5 columns -------------------------------------

ALTER TABLE authorizations
    ADD COLUMN IF NOT EXISTS tenant_id           UUID,
    ADD COLUMN IF NOT EXISTS merchant_id         VARCHAR(64),
    ADD COLUMN IF NOT EXISTS action_kind         VARCHAR(32),
    ADD COLUMN IF NOT EXISTS amount_paise        BIGINT,
    ADD COLUMN IF NOT EXISTS currency            VARCHAR(3)   NOT NULL DEFAULT 'INR',
    -- What the money is about: an order id, a payout id, a campaign id.
    ADD COLUMN IF NOT EXISTS subject_id          VARCHAR(128),
    -- SHA256 over (kind, merchant, amount, currency, subject). An approval is
    -- spendable only against an action that hashes to the same value, which is
    -- what stops an approval for a small amount being presented for a large
    -- one. Indexed, not unique: an expired authorization may legitimately be
    -- re-requested for the same action.
    ADD COLUMN IF NOT EXISTS action_fingerprint  CHAR(64),
    ADD COLUMN IF NOT EXISTS requested_by        VARCHAR(64),
    ADD COLUMN IF NOT EXISTS requested_by_role   VARCHAR(32),
    ADD COLUMN IF NOT EXISTS status              VARCHAR(16),
    ADD COLUMN IF NOT EXISTS required_approvals  SMALLINT     NOT NULL DEFAULT 0,
    -- [{approver_id, role, at}]. An array rather than a child table: it is
    -- read only with its parent, never queried across authorizations, and a
    -- join to answer "who approved this" is a join nobody needs.
    ADD COLUMN IF NOT EXISTS approvers           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- The full decision, rules and all. Stored rather than recomputed: the
    -- policy version that judged this action may no longer exist, and a
    -- decision you can only reproduce under the current policy is not evidence
    -- of what was decided under the old one.
    ADD COLUMN IF NOT EXISTS policy_decision     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS risk                JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS reasons             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS approved_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS consumed_at         TIMESTAMPTZ;

-- The 0001 shape required a session, a scope and a spend cap. A phase 5 record
-- has none of those - it authorizes one specific action, not a budget - so the
-- constraints are relaxed rather than worked around with placeholder values.
-- Placeholder values are how a NOT NULL column ends up full of zeroes that
-- later code mistakes for real limits.
ALTER TABLE authorizations ALTER COLUMN scope            DROP NOT NULL;
ALTER TABLE authorizations ALTER COLUMN max_amount_paise DROP NOT NULL;
-- A denied authorization has no expiry: it is terminal on creation, and an
-- expiry would imply a window in which it could have been used.
ALTER TABLE authorizations ALTER COLUMN expires_at       DROP NOT NULL;

DO $$
BEGIN
    -- Status is a closed set, enforced by the database and not only by the
    -- application. The application is one deploy away from a typo; a row that
    -- said 'aproved' would be permanently unspendable and silently so.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'authorizations_status_valid'
    ) THEN
        ALTER TABLE authorizations ADD CONSTRAINT authorizations_status_valid
            CHECK (status IS NULL OR status IN (
                'pending', 'approved', 'denied', 'consumed', 'expired', 'revoked'
            ));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'authorizations_approvals_sane'
    ) THEN
        ALTER TABLE authorizations ADD CONSTRAINT authorizations_approvals_sane
            CHECK (required_approvals >= 0 AND required_approvals <= 10);
    END IF;

    -- Money moved must be positive where it is recorded at all. The 0001
    -- column keeps its own check; this one covers the phase 5 column.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'authorizations_amount_positive'
    ) THEN
        ALTER TABLE authorizations ADD CONSTRAINT authorizations_amount_positive
            CHECK (amount_paise IS NULL OR amount_paise > 0);
    END IF;

    -- An approved or consumed record must carry the timestamp that says so.
    -- Without this, "approved" is a string nobody can date, and reconstructing
    -- an approval timeline after the fact becomes guesswork.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'authorizations_timestamps_agree'
    ) THEN
        ALTER TABLE authorizations ADD CONSTRAINT authorizations_timestamps_agree
            CHECK (
                (status <> 'approved' OR approved_at IS NOT NULL)
                AND (status <> 'consumed' OR consumed_at IS NOT NULL)
            );
    END IF;
END
$$;

-- The approver queue: "what is waiting on me, oldest first".
CREATE INDEX IF NOT EXISTS idx_authorizations_pending
    ON authorizations (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_authorizations_merchant_status
    ON authorizations (merchant_id, status);
CREATE INDEX IF NOT EXISTS idx_authorizations_fingerprint
    ON authorizations (action_fingerprint);
CREATE INDEX IF NOT EXISTS idx_authorizations_subject
    ON authorizations (subject_id);

-- --- risk_scores -------------------------------------------------------------
-- Every score, kept whether or not it changed anything. The scores that led to
-- *no* action are the ones a model review needs: they are the negative class,
-- and without them the only measurable rate is the false-positive one.

CREATE TABLE IF NOT EXISTS risk_scores (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID,
    merchant_id       VARCHAR(64)  NOT NULL,
    authorization_id  UUID REFERENCES authorizations(id) ON DELETE SET NULL,
    action_kind       VARCHAR(32)  NOT NULL,
    subject_id        VARCHAR(128),
    amount_paise      BIGINT       NOT NULL CHECK (amount_paise >= 0),
    -- NUMERIC, not DOUBLE PRECISION. A float score does not compare equal to
    -- itself across a round trip, and band edges are exactly the comparisons
    -- that would silently disagree with the application's.
    score             NUMERIC(5,4) NOT NULL CHECK (score >= 0 AND score <= 1),
    band              VARCHAR(8)   NOT NULL CHECK (band IN ('low', 'medium', 'high')),
    signals           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    components        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    policy_version    VARCHAR(32),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_scores_tenant_created
    ON risk_scores (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_scores_authorization
    ON risk_scores (authorization_id);
CREATE INDEX IF NOT EXISTS idx_risk_scores_band
    ON risk_scores (merchant_id, band, created_at DESC);

-- --- orders: refund accounting ----------------------------------------------
-- The refund guard subtracts this from the captured amount. Without a stored
-- running total, "how much has already gone back?" is a SUM over a refunds
-- table that does not exist yet, and the guard would compute a ceiling from
-- zero every time - which is how the same money is refunded twice.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS refunded_paise BIGINT NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'orders_refund_within_capture'
    ) THEN
        -- The invariant the guard enforces in code, enforced again here. Code
        -- can be bypassed by a manual UPDATE at 3am during an incident; this
        -- cannot.
        ALTER TABLE orders ADD CONSTRAINT orders_refund_within_capture
            CHECK (refunded_paise >= 0 AND refunded_paise <= final_amount_paise);
    END IF;
END
$$;

-- --- row level security ------------------------------------------------------
-- Same fail-closed policy as every other tenant-owned table: an unpinned query
-- returns zero rows rather than someone else's. `authorizations` was already
-- covered by 0001; `risk_scores` is new and must be enrolled, or it would be
-- the one table in the deployment readable across tenants.

ALTER TABLE risk_scores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON risk_scores;
CREATE POLICY tenant_isolation ON risk_scores
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

COMMIT;
