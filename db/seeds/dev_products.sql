-- Dev catalog seed (run after docs/SCHEMA.sql)
-- Prices in integer paise (INR)

INSERT INTO products (id, sku, merchant_id, name, description, list_price_paise, cost_paise, quantity_on_hand, attributes)
VALUES
  ('prod_hoodie_navy_m', 'HOODIE-NAVY-M', 'merchant_keen', 'Navy Hoodie (M)', 'Soft cotton blend hoodie', 249900, 120000, 50, '{"color": "navy", "size": "M"}'),
  ('prod_hoodie_navy_l', 'HOODIE-NAVY-L', 'merchant_keen', 'Navy Hoodie (L)', 'Soft cotton blend hoodie', 249900, 120000, 35, '{"color": "navy", "size": "L"}'),
  ('prod_tee_white_m', 'TEE-WHITE-M', 'merchant_keen', 'White Tee (M)', 'Classic crew neck', 99900, 45000, 100, '{"color": "white", "size": "M"}')
ON CONFLICT (id) DO NOTHING;
