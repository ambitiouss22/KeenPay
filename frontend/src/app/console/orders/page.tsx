"use client";

/**
 * Look up an order and see exactly what it covers.
 *
 * By id rather than as a list, because the API has no "my orders" route and
 * inventing one in the browser would mean either a list that is not scoped to
 * the caller or a page that lies about being complete. An id is what a buyer
 * is given at checkout, and the lookup is scoped to their merchant server-side:
 * someone else's id answers 404, indistinguishably from one that does not
 * exist.
 *
 * The cost basis is absent from these lines and that is the API's doing, not a
 * decision made here.
 */

import { useState } from "react";
import { Notice, Pill } from "@/components/ui/kit";
import { apiFetch } from "@/lib/api-client";
import { rupees } from "@/lib/format";

interface OrderLine {
  sku?: string;
  name?: string;
  quantity?: number;
  unit_price_paise?: number;
  line_total_paise?: number;
}

interface Order {
  id: string;
  status: string;
  currency?: string;
  final_amount_paise: number;
  line_items: OrderLine[];
  razorpay_payment_link_url?: string | null;
  created_at?: string | null;
  paid_at?: string | null;
}

const TONE: Record<string, "ok" | "stop" | "brand" | "neutral"> = {
  paid: "ok",
  captured: "ok",
  pending: "brand",
  cancelled: "stop",
  failed: "stop",
};

function when(value?: string | null): string {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
}

export default function OrdersPage() {
  const [orderId, setOrderId] = useState("");
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const look = async (event: React.FormEvent) => {
    event.preventDefault();
    const id = orderId.trim();
    if (!id) return;
    setError("");
    setBusy(true);
    try {
      setOrder(await apiFetch<Order>(`/api/v1/orders/${encodeURIComponent(id)}`));
    } catch (e) {
      setOrder(null);
      setError(e instanceof Error ? e.message : "No order with that id");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Orders</h1>
      <p className="mt-2 max-w-3xl text-slate-600">
        Paste the id you were given at checkout. An id belonging to another merchant answers the
        same way as one that never existed.
      </p>

      <form onSubmit={look} className="mt-6 flex max-w-xl gap-2">
        <input
          value={orderId}
          onChange={(e) => setOrderId(e.target.value)}
          placeholder="ord_…"
          className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {busy ? "Looking…" : "Look up"}
        </button>
      </form>

      {error && <Notice kind="bad">{error}</Notice>}

      {order && (
        <div className="mt-6 animate-rise rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-mono text-xs text-slate-500 break-all">{order.id}</p>
              <p className="mt-1 text-2xl font-semibold text-slate-900">
                {rupees(order.final_amount_paise)}
              </p>
            </div>
            <Pill tone={TONE[order.status] ?? "neutral"}>{order.status}</Pill>
          </div>

          <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-500">Placed</dt>
              <dd className="text-slate-900">{when(order.created_at)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Paid</dt>
              <dd className="text-slate-900">{when(order.paid_at)}</dd>
            </div>
          </dl>

          <h2 className="mt-6 text-sm font-semibold text-slate-900">What it covers</h2>
          <ul className="mt-2 divide-y divide-slate-100">
            {order.line_items.map((line, index) => (
              <li
                key={`${line.sku ?? "line"}-${index}`}
                className="flex justify-between gap-4 py-2 text-sm"
              >
                <span className="text-slate-700">
                  {line.name ?? line.sku}
                  <span className="text-slate-400"> × {line.quantity ?? 1}</span>
                </span>
                <span className="font-medium text-slate-900">
                  {typeof line.line_total_paise === "number"
                    ? rupees(line.line_total_paise)
                    : typeof line.unit_price_paise === "number"
                      ? rupees(line.unit_price_paise * (line.quantity ?? 1))
                      : "—"}
                </span>
              </li>
            ))}
          </ul>

          {order.status === "pending" && (
            <Notice kind="info">
              Still pending. Nothing has been charged, and nothing will be until the merchant
              approves this exact amount for these exact goods.
            </Notice>
          )}

          {order.razorpay_payment_link_url && (
            <a
              href={order.razorpay_payment_link_url}
              className="mt-4 inline-flex rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
              rel="noreferrer"
              target="_blank"
            >
              Open the payment link
            </a>
          )}
        </div>
      )}
    </div>
  );
}
