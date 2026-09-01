-- Postgres init: extensions and roles (runs on first container start)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Read-only role for analytics/replicas
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keenpay_readonly') THEN
        CREATE ROLE keenpay_readonly LOGIN PASSWORD 'readonly_change_me';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE keenpay TO keenpay_readonly;
