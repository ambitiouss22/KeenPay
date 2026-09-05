"use client";

/**
 * The buyer's screen: browse, add, check out.
 *
 * Two things it deliberately never does. It never sends a price — the catalogue
 * prices the goods, and the add-item request has no price field to put one in.
 * And it never claims a purchase is finished: checkout produces a *pending
 * order*, which is a request to be paid for, not a charge. Saying "order
 * placed" and leaving it there is how a buyer discovers days later that nothing
 * happened.
 *
 * The cart is created lazily, on the first thing added. An empty cart per page
 * load would litter the merchant's data with carts nobody ever used.
 */

import { useCallback, useEffect, useState } from "react";
import { Notice, Pill } from "@/components/ui/kit";
import { apiFetch } from "@/lib/api-client";
import { rupees } from "@/lib/format";
import { productImage } from "@/lib/product";

interface CatalogProduct {
  id: string;
  sku: string;
  name: string;
  description?: string | null;
  list_price_paise: number;
  quantity_available: number;
  active: boolean;
  // Free-form on the API side; the artwork path lives in here.
  attributes?: Record<string, unknown> | null;
}

interface CartItem {
  item_id: string;
  sku: string;
  name: string;
  unit_price_paise: number;
  quantity: number;
  line_total_paise: number;
}

interface Cart {
  id: string;
  status: string;
  items: CartItem[];
  subtotal_paise: number;
  item_count: number;
}

interface OrderLine {
  sku: string;
  name: string;
  unit_price_paise: number;
  quantity: number;
  line_total_paise: number;
}

interface Order {
  id: string;
  status: string;
  line_items: OrderLine[];
  subtotal_paise: number;
  discount_amount_paise: number;
  final_amount_paise: number;
}

export default function ShopPage() {
  const [query, setQuery] = useState("");
  const [products, setProducts] = useState<CatalogProduct[]>([]);
  const [cart, setCart] = useState<Cart | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const search = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams({ limit: "24" });
      if (query.trim()) params.set("q", query.trim());
      const page = await apiFetch<{ items: CatalogProduct[] }>(
        `/api/v1/catalog/products?${params}`,
      );
      setProducts(page.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the catalogue");
    }
  }, [query]);

  useEffect(() => {
    search();
    // Only on mount: typing should not re-query on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = async (product: CatalogProduct) => {
    setError("");
    setOrder(null);
    setBusy(product.sku);
    try {
      const target = cart ?? (await apiFetch<Cart>("/api/v1/carts", { method: "POST" }));
      const updated = await apiFetch<Cart>(`/api/v1/carts/${target.id}/items`, {
        method: "POST",
        // No price. There is no field for one, and that is the point.
        body: JSON.stringify({ sku: product.sku, quantity: 1 }),
      });
      setCart(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add that item");
    } finally {
      setBusy("");
    }
  };

  const checkout = async () => {
    if (!cart) return;
    setError("");
    setBusy("checkout");
    try {
      const placed = await apiFetch<Order>(`/api/v1/carts/${cart.id}/checkout`, {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: `shop-${cart.id}-${Date.now()}`,
        }),
      });
      setOrder(placed);
      setCart(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Checkout failed");
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Shop</h1>
      <p className="mt-2 max-w-3xl text-slate-600">
        Every price here comes from the merchant&apos;s catalogue. Checking out creates a pending
        order — it is not a charge.
      </p>
      {error && <Notice kind="bad">{error}</Notice>}

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_320px]">
        {/* --- catalogue --- */}
        <div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              search();
            }}
            className="mb-4 flex gap-2"
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the catalogue"
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
            />
            <button
              type="submit"
              className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50"
            >
              Search
            </button>
          </form>

          {products.length === 0 ? (
            <p className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500 shadow-card">
              Nothing matched. The merchant may not have added anything yet.
            </p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {products.map((product) => {
                const out = product.quantity_available <= 0 || !product.active;
                return (
                  <li
                    key={product.id}
                    className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card"
                  >
                    {/* Decorative: the product name sits right below it, so an
                        alt text here would only repeat itself to a screen
                        reader. Plain <img> on purpose - these are ~1KB local
                        SVGs, and next/image would refuse to optimise SVG
                        without loosening the image config for no gain. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={productImage(product.attributes)}
                      alt=""
                      loading="lazy"
                      className={`aspect-square w-full object-cover ${out ? "opacity-50 grayscale" : ""}`}
                    />
                    <div className="flex flex-1 flex-col p-4">
                      <div className="flex items-start justify-between gap-2">
                        <h2 className="font-semibold text-slate-900">{product.name}</h2>
                        {out && <Pill tone="stop">Unavailable</Pill>}
                      </div>
                      <p className="mt-1 font-mono text-xs text-slate-500">{product.sku}</p>
                      {product.description && (
                        <p className="mt-2 text-sm text-slate-600">{product.description}</p>
                      )}
                      <div className="mt-auto flex items-end justify-between pt-4">
                        <span className="text-lg font-semibold text-slate-900">
                          {rupees(product.list_price_paise)}
                        </span>
                        <button
                          onClick={() => add(product)}
                          disabled={out || busy === product.sku}
                          className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
                        >
                          {busy === product.sku ? "Adding…" : "Add"}
                        </button>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">
                        {product.quantity_available} available
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* --- cart / order --- */}
        <aside className="lg:sticky lg:top-20 lg:self-start">
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
            <h2 className="text-base font-semibold text-slate-900">Your cart</h2>
            {!cart || cart.items.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">Nothing in it yet.</p>
            ) : (
              <>
                <ul className="mt-3 divide-y divide-slate-100">
                  {cart.items.map((item) => (
                    <li key={item.item_id} className="flex justify-between gap-3 py-2 text-sm">
                      <span className="text-slate-700">
                        {item.name}
                        <span className="text-slate-400"> × {item.quantity}</span>
                      </span>
                      <span className="font-medium text-slate-900">
                        {rupees(item.line_total_paise)}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 flex justify-between border-t border-slate-200 pt-3 text-sm font-semibold">
                  <span>Subtotal</span>
                  <span>{rupees(cart.subtotal_paise)}</span>
                </div>
                <button
                  onClick={checkout}
                  disabled={busy === "checkout"}
                  className="mt-4 w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
                >
                  {busy === "checkout" ? "Placing…" : "Check out"}
                </button>
                <p className="mt-2 text-xs text-slate-500">
                  This creates a pending order. No money moves until the merchant approves it.
                </p>
              </>
            )}
          </div>

          {order && (
            <div className="mt-4 animate-rise rounded-xl border border-ok-200 bg-ok-50 p-5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-base font-semibold text-ok-700">Order placed</h2>
                <Pill tone="ok">{order.status}</Pill>
              </div>
              <p className="mt-2 font-mono text-xs text-slate-600 break-all">{order.id}</p>
              <div className="mt-3 flex justify-between text-sm font-semibold text-slate-900">
                <span>Total</span>
                <span>{rupees(order.final_amount_paise)}</span>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-slate-600">
                Nothing has been charged. The merchant must authorize this exact amount for these
                exact goods before any money moves — and that approval cannot be spent on anything
                else. Keep the id above to track it.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
