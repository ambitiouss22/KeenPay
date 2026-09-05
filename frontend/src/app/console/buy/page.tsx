"use client";

import { useState } from "react";
import { mcp } from "@/lib/gateway";
import { rupees } from "@/lib/format";
import { Button, Card, Field, Notice, ui } from "@/components/ui/kit";

interface Step {
  label: string;
  status: "ok" | "bad" | "info";
  detail?: string;
}

interface Authorization {
  id: string;
  status: string;
  amount_paise: number;
  required_approvals?: number;
  reasons?: string[];
  policy_decision?: { outcome?: string };
  risk?: { band?: string; score?: number };
}

export default function BuyPage() {
  const [query, setQuery] = useState("hoodie");
  const [qty, setQty] = useState("2");
  const [steps, setSteps] = useState<Step[]>([]);
  const [authz, setAuthz] = useState<Authorization | null>(null);
  const [running, setRunning] = useState(false);

  const push = (s: Step) => setSteps((prev) => [...prev, s]);

  const run = async () => {
    setSteps([]);
    setAuthz(null);
    setRunning(true);
    try {
      const quantity = Math.max(1, Number(qty) || 1);

      const found = await mcp<{ items: Array<{ sku: string; name: string }> }>("search_products", {
        query,
        limit: 10,
      });
      const foundItems = found.data?.items ?? [];
      if (!found.ok || foundItems.length === 0) {
        push({ label: "Search the catalogue", status: "bad", detail: found.error ?? "no products" });
        return;
      }
      const pick = foundItems[0];
      push({ label: "Discovered products", status: "ok", detail: `buying ${pick.sku}` });

      const cart = await mcp<{ id: string }>("create_cart");
      if (!cart.ok || !cart.data) {
        push({ label: "Open a cart", status: "bad", detail: cart.error });
        return;
      }
      push({ label: "Opened a cart", status: "ok", detail: cart.data.id });

      const add = await mcp("add_item", { cart_id: cart.data.id, sku: pick.sku, quantity });
      if (!add.ok) {
        push({ label: "Add to cart", status: "bad", detail: add.error });
        return;
      }
      push({ label: `Added ${quantity} unit(s)`, status: "ok", detail: "priced by the Control Plane" });

      const key = "ui-" + Math.random().toString(16).slice(2);
      const out = await mcp<{ id: string; final_amount_paise: number }>("checkout", {
        cart_id: cart.data.id,
        idempotency_key: key,
      });
      if (!out.ok || !out.data) {
        push({ label: "Checkout", status: "bad", detail: out.error });
        return;
      }
      push({
        label: "Checked out to a PENDING order",
        status: "ok",
        detail: `total ${rupees(out.data.final_amount_paise)} — nothing charged`,
      });

      const auth = await mcp<Authorization>("request_authorization", {
        order_id: out.data.id,
        amount_paise: out.data.final_amount_paise,
      });
      if (!auth.ok || !auth.data) {
        push({ label: "Request authorization", status: "bad", detail: auth.error });
        return;
      }
      setAuthz(auth.data);
      push({
        label: "Requested authorization",
        status: auth.data.status === "denied" ? "info" : "ok",
        detail: `status = ${auth.data.status.toUpperCase()}`,
      });

      const blocked = await mcp("capture_payment", { order_id: out.data.id });
      push({
        label: "Tried to capture the payment directly",
        status: "bad",
        detail: `REFUSED — ${blocked.error ?? "money-moving action is not allowed"}`,
      });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div>
      <Card title="AI buyer (via the protocol gateway)">
        <p style={{ color: ui.mut, marginTop: 0 }}>
          An agent discovers, carts, checks out and requests authorization — all as MCP messages
          through the gateway. It has no tool that moves money.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr auto", gap: 12, alignItems: "end" }}>
          <Field label="What to buy" value={query} onChange={setQuery} />
          <Field label="Quantity" value={qty} onChange={setQty} type="number" />
          <Button onClick={run} disabled={running} style={{ marginBottom: 10 }}>
            {running ? "Running…" : "Run the AI buyer"}
          </Button>
        </div>
      </Card>

      {steps.length > 0 && (
        <Card title="Trace">
          {steps.map((s, i) => (
            <div
              key={i}
              style={{
                borderLeft: `3px solid ${s.status === "ok" ? ui.ok : s.status === "bad" ? ui.bad : ui.mut}`,
                padding: "6px 12px",
                marginBottom: 8,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 14 }}>{s.label}</div>
              {s.detail && <div style={{ color: ui.mut, fontSize: 13 }}>{s.detail}</div>}
            </div>
          ))}
        </Card>
      )}

      {authz && (
        <Card title="The money action — explainable, bounded, gated">
          <table style={{ width: "100%", fontSize: 14 }}>
            <tbody>
              <tr>
                <td style={{ color: ui.mut, padding: "4px 0", width: 180 }}>Authorization</td>
                <td style={{ fontFamily: "monospace" }}>{authz.id}</td>
              </tr>
              <tr>
                <td style={{ color: ui.mut, padding: "4px 0" }}>Amount</td>
                <td>{rupees(authz.amount_paise)}</td>
              </tr>
              <tr>
                <td style={{ color: ui.mut, padding: "4px 0" }}>Policy decision</td>
                <td>{authz.policy_decision?.outcome ?? authz.status}</td>
              </tr>
              <tr>
                <td style={{ color: ui.mut, padding: "4px 0" }}>Risk</td>
                <td>
                  {authz.risk?.band
                    ? `${authz.risk.band} (score ${authz.risk.score ?? "-"})`
                    : "not scored (a denial is categorical)"}
                </td>
              </tr>
              <tr>
                <td style={{ color: ui.mut, padding: "4px 0" }}>Human sign-offs</td>
                <td>{authz.required_approvals ?? 0} required before capture</td>
              </tr>
            </tbody>
          </table>
          {authz.reasons && authz.reasons.length > 0 && (
            <Notice kind="info">{authz.reasons.join("; ")}</Notice>
          )}
        </Card>
      )}
    </div>
  );
}
