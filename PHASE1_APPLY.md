# Phase 1 — apply and verify

Unzip into `C:\Dev\KeenPay`, preserving paths. Files land at:

```
db/init.sql                                  (new)
db/migrations/0001_initial.sql               (replaces the marker stub)
db/seeds/dev_products.sql                    (replaces)
api/db/__init__.py                           (new)
api/db/models.py                             (new — canonical schema)
api/db/session.py                            (new)
api/db/repositories.py                       (new)
api/core/db.py                               (new)
api/core/rls.py                              (new)
api/dependencies/db.py                       (replaces)
api/tests/unit/test_rls_helpers.py           (new)
api/tests/integration/test_tenant_isolation.py (new)
```

Nothing under `api/repositories/` is touched. The v1 code paths keep working.

---

## 1. Start Postgres

```powershell
cd C:\Dev\KeenPay
docker compose -f deploy/compose/docker-compose.dev.yml up -d postgres
```

## 2. Create the roles (superuser, once)

```powershell
docker compose -f deploy/compose/docker-compose.dev.yml exec -T postgres `
  psql -U postgres -d keenpay -v ON_ERROR_STOP=1 < db/init.sql
```

Creates `keenpay_migration` (owns the schema) and `keenpay_app` (runtime,
`NOBYPASSRLS`). Dev passwords are in the file — change them before any
deployed environment.

## 3. Apply the migration

```powershell
docker compose -f deploy/compose/docker-compose.dev.yml exec -T postgres `
  psql -U keenpay_migration -d keenpay -v ON_ERROR_STOP=1 < db/migrations/0001_initial.sql
```

Safe on a fresh database and on one that already ran `docs/SCHEMA.sql` — tables
use `IF NOT EXISTS`, and `tenant_id` is added and backfilled in place.

The `NOTICE: ... does not exist, skipping` lines on a fresh database are the
`DROP ... IF EXISTS` guards. Expected.

## 4. Seed

```powershell
docker compose -f deploy/compose/docker-compose.dev.yml exec -T postgres `
  psql -U keenpay_migration -d keenpay -v ON_ERROR_STOP=1 < db/seeds/dev_products.sql
```

Two tenants: `merchant_keen` (5 products, 4 users) and `merchant_acme`
(2 products, 1 user). The second exists so isolation has something to fail
against — a single-tenant fixture cannot detect a leak.

## 5. Run the isolation tests

They must connect as `keenpay_app`. As the owner or a superuser they would pass
without RLS doing anything, so the suite refuses to run privileged.

```powershell
$env:KEENPAY_TEST_DATABASE_URL="postgresql+asyncpg://keenpay_app:keenpay_app_dev_only@localhost:5432/keenpay"
pytest api/tests/integration/test_tenant_isolation.py api/tests/unit/test_rls_helpers.py -v
```

Expect **30 passed**. They skip if no database is reachable — a skip is not a
pass, so check the summary line.

## 6. Confirm nothing regressed

```powershell
pytest api/tests/ -q
ruff check api/
```

---

## Verify by hand

```sql
-- No KeenPay role may bypass RLS. Must be empty.
SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolname LIKE 'keenpay%';

-- Every tenant table has RLS on. Must be empty.
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relrowsecurity
   AND EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name=c.relname AND column_name='tenant_id');
```

As `keenpay_app`:

```sql
-- Unpinned: 0. Fail-closed.
SELECT count(*) FROM products;

-- Pinned: only that tenant's rows.
BEGIN;
SELECT set_config('app.tenant_id',
                  (SELECT id::text FROM tenants WHERE slug='merchant_keen'), true);
SELECT sku FROM products;   -- no ACME-* rows
COMMIT;
```

---

## Using it in code

```python
from db.session import tenant_session
from db.repositories import OrderRepository, CampaignRepository

async with tenant_session(tenant_id) as session:
    orders = await OrderRepository(session).list_recent()
```

In a route:

```python
from dependencies.db import TenantDb

@router.get("/orders")
async def list_orders(db: TenantDb):
    return await OrderRepository(db).list_recent()
```

The tenant comes from the verified token, never from a header or body field.

---

## Two things to know

**`get_db` now reads nothing from tenant tables.** RLS is fail-closed, and the
legacy dependency pins no tenant. That is deliberate: a forgotten pin returns an
empty result, which is visible, rather than another tenant's rows, which is not.
Anything still on `get_db` that touches tenant data needs moving to
`get_tenant_db`. Nothing does today — `api/repositories/*` run in in-memory mode
under test — but it will bite when those paths hit Postgres.

**CI does not run the isolation tests yet.** They skip without a database, so
`API CI` stays green but proves nothing about RLS. To make them count, add a
service to `.github/workflows/api-ci.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: keenpay
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready --health-interval 10s
      --health-timeout 5s --health-retries 5
```

then apply `init.sql`, `0001_initial.sql` and the seed before `pytest`, with
`KEENPAY_TEST_DATABASE_URL` pointing at `keenpay_app`. Say the word and I'll
write that workflow change.

---

## Commit

```powershell
git add db/init.sql db/migrations/0001_initial.sql db/seeds/dev_products.sql
git commit -m "feat(db): add tenants, tenant_id and row-level security policies"

git add api/db/ api/core/rls.py api/core/db.py
git commit -m "feat(db): add orm models, tenant-pinned sessions and scoped repositories"

git add api/dependencies/db.py
git commit -m "feat(db): resolve tenant from principal and pin the request session"

git add api/tests/unit/test_rls_helpers.py api/tests/integration/test_tenant_isolation.py
git commit -m "test(db): prove cross-tenant isolation and atomic reserve under concurrency"

git push origin main
```
