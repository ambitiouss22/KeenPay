-- =============================================================================
-- KeenPay — database roles (run ONCE per cluster, as a superuser)
-- =============================================================================
-- Docker: mount at /docker-entrypoint-initdb.d/00-init.sql so it runs before
-- migrations. Manual: psql -U postgres -d keenpay -f db/init.sql
--
-- Two roles, deliberately separated:
--
--   keenpay_migration  Owns every schema object. Runs migrations and seeds.
--                      As the table owner it is exempt from RLS unless a table
--                      declares FORCE ROW LEVEL SECURITY, which is what lets it
--                      backfill and seed across tenants.
--
--   keenpay_app        The runtime role the API connects as. NOT a superuser and
--                      explicitly NOBYPASSRLS, so every row-level policy applies
--                      to it with no escape hatch. It owns nothing.
--
-- The separation is the whole point: if the API is compromised it still cannot
-- read another tenant's rows, because the database — not application code — is
-- what refuses.
--
-- Passwords below are DEV DEFAULTS. In any deployed environment create the roles
-- out of band with real secrets, or ALTER ROLE ... PASSWORD immediately after.
-- =============================================================================

-- --- keenpay_migration --------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keenpay_migration') THEN
        CREATE ROLE keenpay_migration
            LOGIN
            PASSWORD 'keenpay_migration_dev_only'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOBYPASSRLS;
    END IF;
END
$$;

-- --- keenpay_app --------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keenpay_app') THEN
        CREATE ROLE keenpay_app
            LOGIN
            PASSWORD 'keenpay_app_dev_only'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOBYPASSRLS;
    END IF;
END
$$;

-- Belt and braces: if the roles already existed from an earlier setup, force the
-- security-relevant attributes back to what we require. BYPASSRLS on either role
-- would silently defeat every tenant policy in the schema.
ALTER ROLE keenpay_migration NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
ALTER ROLE keenpay_app       NOSUPERUSER NOBYPASSRLS NOCREATEROLE;

-- --- extensions ---------------------------------------------------------------
-- CREATE EXTENSION needs privileges on the database itself, which a deliberately
-- unprivileged migration role does not have. So extensions are installed here,
-- in the one script that legitimately runs as a superuser, and the migration
-- only checks that they are present.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- --- schema ownership ---------------------------------------------------------
-- Migrations create objects in public; keenpay_migration must own it.
ALTER SCHEMA public OWNER TO keenpay_migration;

GRANT USAGE ON SCHEMA public TO keenpay_app;

-- keenpay_app gets DML only. No DDL, ever — schema changes go through migrations.
-- Default privileges cover tables created later by keenpay_migration, so the app
-- role does not need re-granting after every migration.
ALTER DEFAULT PRIVILEGES FOR ROLE keenpay_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO keenpay_app;

ALTER DEFAULT PRIVILEGES FOR ROLE keenpay_migration IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO keenpay_app;

-- Revoke the implicit PUBLIC create right on the public schema; without this any
-- role could create objects there.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- =============================================================================
-- Verification (run after migrations; both should return zero rows)
-- =============================================================================
--
-- Roles that can bypass RLS:
--   SELECT rolname FROM pg_roles
--    WHERE rolbypassrls AND rolname LIKE 'keenpay%';
--
-- Tenant tables with RLS switched off:
--   SELECT c.relname
--     FROM pg_class c
--     JOIN pg_namespace n ON n.oid = c.relnamespace
--    WHERE n.nspname = 'public'
--      AND c.relkind = 'r'
--      AND NOT c.relrowsecurity
--      AND EXISTS (
--          SELECT 1 FROM information_schema.columns
--           WHERE table_name = c.relname AND column_name = 'tenant_id'
--      );
-- =============================================================================
