# Database

## Canonical schema

**Source of truth:** `docs/SCHEMA.sql`

Do not maintain a second full DDL elsewhere. `db/migrations/` holds incremental changes only.

## Bootstrap (fresh database)

```bash
psql $DATABASE_URL -f docs/SCHEMA.sql
psql $DATABASE_URL -f db/seeds/dev_products.sql
```

`docs/SCHEMA.sql` includes core commerce tables and auth tables (`users`, `refresh_tokens`, `api_keys`, `auth_audit_log`).

Or: `make bootstrap`

## Migrations workflow

1. Add numbered SQL file: `db/migrations/0002_add_foo.sql`
2. Apply in order on staging before production
3. Update `docs/SCHEMA.sql` when the change is merged (keep docs in sync)

## Seeds

`db/seeds/` — dev/staging data only. Never run seeds in production.
