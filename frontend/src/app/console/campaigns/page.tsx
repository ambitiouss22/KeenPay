"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { rupees, toPaise } from "@/lib/format";
import { Button, Card, Field, Notice, ui } from "@/components/ui/kit";

interface Campaign {
  id: string;
  name?: string;
  budget_paise?: number;
  code?: string;
  [key: string]: unknown;
}

interface Opportunity {
  id?: string;
  kind?: string;
  title?: string;
  rationale?: string;
  sku?: string;
  [key: string]: unknown;
}

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [name, setName] = useState("Spring Push");
  const [budget, setBudget] = useState("500.00");
  const [reserveAmt, setReserveAmt] = useState("100.00");
  const [selected, setSelected] = useState<string>("");

  const load = useCallback(async () => {
    setError("");
    try {
      const c = await apiFetch<{ items: Campaign[] }>("/api/v1/campaigns?limit=50");
      setCampaigns(c.items);
      if (c.items[0] && !selected) setSelected(c.items[0].id);
      const o = await apiFetch<{ items: Opportunity[] }>("/api/v1/opportunities?limit=50");
      setOpps(o.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load growth data");
    }
  }, [selected]);

  useEffect(() => {
    load();
  }, [load]);

  const createCampaign = async () => {
    setError("");
    setNotice("");
    try {
      await apiFetch<Campaign>("/api/v1/campaigns", {
        method: "POST",
        body: JSON.stringify({ name, budget_paise: toPaise(budget) }),
      });
      setNotice(`Opened campaign “${name}” with a ${rupees(toPaise(budget))} cap`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed (needs manager/admin)");
    }
  };

  const reserve = async () => {
    setError("");
    setNotice("");
    if (!selected) return;
    try {
      await apiFetch(`/api/v1/campaigns/${selected}/reserve`, {
        method: "POST",
        body: JSON.stringify({
          amount_paise: toPaise(reserveAmt),
          idempotency_key: "res-" + Math.random().toString(16).slice(2),
        }),
      });
      setNotice(`Reserved ${rupees(toPaise(reserveAmt))} — budget held atomically`);
    } catch (e) {
      // The Control Plane answers 409 when the cap cannot fund it.
      setError(
        (e instanceof Error ? e.message : "Reserve failed") +
          " — the cap cannot be overspent",
      );
    }
  };

  const generate = async () => {
    setError("");
    setNotice("");
    try {
      const res = await apiFetch<{ generated: number }>("/api/v1/opportunities/generate", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setNotice(`Generated ${res.generated} suggestion(s)`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generate failed (needs manager/admin)");
    }
  };

  return (
    <div>
      {error && (
        <Card>
          <Notice kind="bad">{error}</Notice>
        </Card>
      )}
      {notice && (
        <Card>
          <Notice kind="ok">{notice}</Notice>
        </Card>
      )}

      <Card title="Campaigns (capped budgets)">
        <table style={{ width: "100%", fontSize: 14, marginBottom: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: ui.mut }}>
              <th style={{ padding: "6px 8px" }}></th>
              <th style={{ padding: "6px 8px" }}>Name</th>
              <th style={{ padding: "6px 8px" }}>Cap</th>
              <th style={{ padding: "6px 8px" }}>ID</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((c) => (
              <tr key={c.id} style={{ borderTop: `1px solid ${ui.line}` }}>
                <td style={{ padding: "6px 8px" }}>
                  <input
                    type="radio"
                    name="campaign"
                    checked={selected === c.id}
                    onChange={() => setSelected(c.id)}
                  />
                </td>
                <td style={{ padding: "6px 8px" }}>{c.name ?? "—"}</td>
                <td style={{ padding: "6px 8px" }}>
                  {typeof c.budget_paise === "number" ? rupees(c.budget_paise) : "—"}
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "monospace", color: ui.mut }}>{c.id}</td>
              </tr>
            ))}
            {campaigns.length === 0 && (
              <tr>
                <td colSpan={4} style={{ padding: "12px 8px", color: ui.mut }}>
                  No campaigns yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div style={{ display: "flex", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
          <div style={{ width: 160 }}>
            <Field label="Reserve (₹)" value={reserveAmt} onChange={setReserveAmt} />
          </div>
          <Button onClick={reserve} disabled={!selected} style={{ marginBottom: 10 }}>
            Reserve budget
          </Button>
        </div>
      </Card>

      <Card title="Open a campaign">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 16px" }}>
          <Field label="Name" value={name} onChange={setName} />
          <Field label="Budget cap (₹)" value={budget} onChange={setBudget} />
        </div>
        <Button onClick={createCampaign} disabled={!name}>
          Open campaign
        </Button>
      </Card>

      <Card
        title="Opportunities"
        right={<Button variant="ghost" onClick={generate}>Generate</Button>}
      >
        {opps.length === 0 && <p style={{ color: ui.mut, margin: 0 }}>No suggestions yet.</p>}
        {opps.map((o, i) => (
          <div key={o.id ?? i} style={{ borderTop: i ? `1px solid ${ui.line}` : "none", padding: "8px 0" }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{o.kind ?? o.title ?? "opportunity"}</div>
            <div style={{ color: ui.mut, fontSize: 13 }}>
              {o.rationale ?? o.title ?? o.sku ?? o.id ?? ""}
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}
