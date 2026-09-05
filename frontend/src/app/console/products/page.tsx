"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { rupees, toPaise } from "@/lib/format";
import { Button, Card, Field, Notice, ui } from "@/components/ui/kit";
import { productImage } from "@/lib/product";

interface Product {
  id?: string;
  sku: string;
  name: string;
  list_price_paise: number;
  quantity_available?: number;
  quantity_on_hand?: number;
  active?: boolean;
  attributes?: Record<string, unknown> | null;
}

interface ProductList {
  items: Product[];
  total: number;
}

export default function ProductsPage() {
  const [items, setItems] = useState<Product[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // new product form
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [cost, setCost] = useState("");
  const [stock, setStock] = useState("10");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (query) params.set("q", query);
      const data = await apiFetch<ProductList>(`/api/v1/catalog/products?${params}`);
      setItems(data.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load products");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    setNotice("");
    setError("");
    try {
      await apiFetch<Product>("/api/v1/products", {
        method: "POST",
        body: JSON.stringify({
          sku,
          name,
          list_price_paise: toPaise(price),
          cost_paise: toPaise(cost),
          quantity_on_hand: Number(stock) || 0,
        }),
      });
      setNotice(`Created ${sku}`);
      setSku("");
      setName("");
      setPrice("");
      setCost("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed (needs admin)");
    }
  };

  return (
    <div>
      <Card
        title="Catalogue"
        right={
          <div style={{ display: "flex", gap: 8 }}>
            <input
              value={query}
              placeholder="Search…"
              onChange={(e) => setQuery(e.target.value)}
              style={{ padding: "8px 10px", border: `1px solid ${ui.line}`, borderRadius: 8 }}
            />
            <Button variant="ghost" onClick={load} disabled={loading}>
              {loading ? "…" : "Refresh"}
            </Button>
          </div>
        }
      >
        {error && <Notice kind="bad">{error}</Notice>}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ textAlign: "left", color: ui.mut }}>
              <th style={{ padding: "6px 8px", width: 56 }} aria-label="Image" />
              <th style={{ padding: "6px 8px" }}>SKU</th>
              <th style={{ padding: "6px 8px" }}>Name</th>
              <th style={{ padding: "6px 8px" }}>Price</th>
              <th style={{ padding: "6px 8px" }}>Stock</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.sku} style={{ borderTop: `1px solid ${ui.line}` }}>
                <td style={{ padding: "6px 8px" }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={productImage(p.attributes)}
                    alt=""
                    width={40}
                    height={40}
                    loading="lazy"
                    style={{ display: "block", borderRadius: 8, border: `1px solid ${ui.line}` }}
                  />
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{p.sku}</td>
                <td style={{ padding: "6px 8px" }}>{p.name}</td>
                <td style={{ padding: "6px 8px" }}>{rupees(p.list_price_paise)}</td>
                <td style={{ padding: "6px 8px" }}>
                  {p.quantity_available ?? p.quantity_on_hand ?? "-"}
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={5} style={{ padding: "12px 8px", color: ui.mut }}>
                  No products yet — add one below.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <Card title="Add a product">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 16px" }}>
          <Field label="SKU" value={sku} onChange={setSku} placeholder="TEA-GREEN-100" />
          <Field label="Name" value={name} onChange={setName} placeholder="Green Tea 100g" />
          <Field label="List price (₹)" value={price} onChange={setPrice} placeholder="249.00" />
          <Field label="Cost (₹)" value={cost} onChange={setCost} placeholder="120.00" />
          <Field label="Stock" value={stock} onChange={setStock} type="number" />
        </div>
        {notice && <Notice kind="ok">{notice}</Notice>}
        <Button onClick={create} disabled={!sku || !name}>
          Create product
        </Button>
      </Card>
    </div>
  );
}
