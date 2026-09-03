-- =============================================================================
-- KeenPay — development seed
-- =============================================================================
-- Run as keenpay_migration, after db/migrations/0001_initial.sql:
--
--   psql "postgresql://keenpay_migration:...@localhost:5432/keenpay" \
--        -v ON_ERROR_STOP=1 -f db/seeds/dev_products.sql
--
-- Seeds TWO tenants on purpose. A single-tenant fixture cannot catch a leak,
-- because with one tenant every query trivially returns "only that tenant's"
-- rows whether the policy works or not. The isolation tests need a second
-- tenant with data of its own to have anything to fail against.
--
--   merchant_keen  -> the demo merchant the app runs as
--   merchant_acme  -> the neighbour that must never be visible
--
-- Idempotent: safe to re-run.
--
-- Dev password for every seeded user is KeenPayDev1! — the hash below is a
-- bcrypt digest of exactly that, and it is fine in a seed precisely because it
-- is public knowledge and worthless. Never seed a real credential here.
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- --- tenants ------------------------------------------------------------------

INSERT INTO tenants (slug, name, settings) VALUES
    ('merchant_keen', 'KeenPay Demo Merchant',
     '{"max_discount_pct": 15.0, "currency": "INR"}'::jsonb),
    ('merchant_acme', 'Acme Corp (isolation fixture)',
     '{"max_discount_pct": 5.0, "currency": "INR"}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

-- --- users --------------------------------------------------------------------

INSERT INTO users (id, tenant_id, merchant_id, email, password_hash, role, display_name)
SELECT v.id, t.id, 'merchant_keen', v.email, v.pw, v.role::user_role, v.display_name
  FROM tenants t
  CROSS JOIN (VALUES
      ('user_dev_shopper', 'shopper@keenpay.dev',
       '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQKHyL4GqK0i',
       'shopper', 'Dev Shopper'),
      ('user_dev_support', 'support@keenpay.dev',
       '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQKHyL4GqK0i',
       'support_agent', 'Dev Support'),
      ('user_dev_manager', 'manager@keenpay.dev',
       '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQKHyL4GqK0i',
       'manager', 'Dev Manager'),
      ('user_dev_admin', 'admin@keenpay.dev',
       '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQKHyL4GqK0i',
       'admin', 'Dev Admin')
  ) AS v(id, email, pw, role, display_name)
 WHERE t.slug = 'merchant_keen'
ON CONFLICT (merchant_id, email) DO NOTHING;

-- One user on the other side of the fence, so tests can assert that listing
-- users as merchant_keen never turns this row up.
INSERT INTO users (id, tenant_id, merchant_id, email, password_hash, role, display_name)
SELECT 'user_acme_admin', t.id, 'merchant_acme', 'admin@acme.test',
       '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQKHyL4GqK0i',
       'admin'::user_role, 'Acme Admin'
  FROM tenants t WHERE t.slug = 'merchant_acme'
ON CONFLICT (merchant_id, email) DO NOTHING;

-- --- catalog: merchant_keen ---------------------------------------------------

INSERT INTO products (
    id, tenant_id, merchant_id, sku, name, description,
    list_price_paise, cost_paise, quantity_on_hand, attributes
)
SELECT v.id, t.id, 'merchant_keen', v.sku, v.name, v.description,
       v.list_price, v.cost, v.qty, v.attrs::jsonb
  FROM tenants t
  CROSS JOIN (VALUES
      ('prod_001', 'HOODIE-NAVY-M', 'Keen Hoodie Navy M',
       'Premium cotton hoodie, navy, medium', 249900, 120000, 50,
       '{"color": "navy", "size": "M", "category": "apparel"}'),
      ('prod_002', 'HOODIE-NAVY-L', 'Keen Hoodie Navy L',
       'Premium cotton hoodie, navy, large', 249900, 120000, 35,
       '{"color": "navy", "size": "L", "category": "apparel"}'),
      ('prod_003', 'TEE-BLACK-M', 'Keen Tee Black M',
       'Organic cotton t-shirt, black, medium', 99900, 45000, 100,
       '{"color": "black", "size": "M", "category": "apparel"}'),
      ('prod_004', 'CAP-WHITE-OS', 'Keen Cap White',
       'Adjustable dad cap, white', 79900, 35000, 80,
       '{"color": "white", "size": "OS", "category": "accessories"}'),
      ('prod_005', 'BAG-TOTE-NAT', 'Keen Tote Natural',
       'Canvas tote bag, natural', 129900, 55000, 40,
       '{"color": "natural", "category": "accessories"}')
  ) AS v(id, sku, name, description, list_price, cost, qty, attrs)
 WHERE t.slug = 'merchant_keen'
ON CONFLICT (merchant_id, sku) DO NOTHING;

-- --- catalog: merchant_acme (isolation fixture) -------------------------------
-- Deliberately recognisable. If one of these SKUs ever appears in a
-- merchant_keen query result, tenant isolation is broken.

INSERT INTO products (
    id, tenant_id, merchant_id, sku, name, description,
    list_price_paise, cost_paise, quantity_on_hand, attributes
)
SELECT v.id, t.id, 'merchant_acme', v.sku, v.name, v.description,
       v.list_price, v.cost, v.qty, v.attrs::jsonb
  FROM tenants t
  CROSS JOIN (VALUES
      ('prod_acme_001', 'ACME-ANVIL', 'Acme Anvil',
       'Should never be visible to merchant_keen', 999900, 500000, 5,
       '{"category": "hardware", "isolation_canary": true}'),
      ('prod_acme_002', 'ACME-ROCKET', 'Acme Rocket Skates',
       'Should never be visible to merchant_keen', 1499900, 700000, 3,
       '{"category": "hardware", "isolation_canary": true}')
  ) AS v(id, sku, name, description, list_price, cost, qty, attrs)
 WHERE t.slug = 'merchant_acme'
ON CONFLICT (merchant_id, sku) DO NOTHING;

-- --- campaigns ----------------------------------------------------------------
-- Small budget on purpose: the concurrency test needs a ceiling it can actually
-- reach, so that racing reservations have something to contend over.

INSERT INTO campaigns (id, tenant_id, name, code, budget_paise, max_discount_pct)
SELECT '11111111-1111-1111-1111-111111111111'::uuid, t.id,
       'Launch Discount Pool', 'LAUNCH15', 100000, 15.0
  FROM tenants t WHERE t.slug = 'merchant_keen'
ON CONFLICT (id) DO NOTHING;

INSERT INTO campaigns (id, tenant_id, name, code, budget_paise, max_discount_pct)
SELECT '22222222-2222-2222-2222-222222222222'::uuid, t.id,
       'Acme Promo (isolation fixture)', 'ACME5', 500000, 5.0
  FROM tenants t WHERE t.slug = 'merchant_acme'
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- =============================================================================
-- What was seeded
-- =============================================================================
--   SELECT t.slug,
--          (SELECT count(*) FROM products p WHERE p.tenant_id = t.id)  AS products,
--          (SELECT count(*) FROM users    u WHERE u.tenant_id = t.id)  AS users,
--          (SELECT count(*) FROM campaigns c WHERE c.tenant_id = t.id) AS campaigns
--     FROM tenants t ORDER BY t.slug;
-- =============================================================================
