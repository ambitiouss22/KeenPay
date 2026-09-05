"use client";

/**
 * The evidence page.
 *
 * Two independent proofs live here, and they check different things.
 *
 * The ledger is hash-chained: every entry carries the hash of the one before
 * it, so an entry cannot be edited, reordered or removed without breaking
 * every hash after it. `/api/v1/audit/verify` walks the whole chain and says
 * where it broke. The head hash is the single value an auditor keeps: hold on
 * to it today and you can prove tomorrow that nothing before it was touched.
 *
 * A passport is narrower and stronger. It is one payment's own signed record,
 * and `/api/v1/passport/verify` checks the signature against the body without
 * consulting the database at all. That is what "offline-verifiable" means, and
 * the Tamper button below proves it: change one paisa in the body and
 * the same endpoint that just said `valid` says `invalid`. Verification that
 * only ever passes proves nothing, so this page makes it fail on demand.
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { Button, Card, Field, Notice, ui } from "@/components/ui/kit";

interface LedgerEntry {
  seq: number;
  merchant_id: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  action: string;
  payload: Record<string, unknown>;
  recorded_at: string;
  prev_hash: string;
  entry_hash: string;
  correlation_id?: string | null;
}

interface LedgerPage {
  entries: LedgerEntry[];
  total: number;
  limit: number;
  offset: number;
  head_hash: string;
}

interface ChainVerification {
  merchant_id: string;
  valid: boolean;
  entry_count: number;
  head_hash: string;
  errors: string[];
}

interface Passport {
  body: Record<string, unknown>;
  signature: { algorithm: string; body_hash: string; value: string };
}

interface PassportVerdict {
  valid: boolean;
  errors: string[];
}

const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

/** Hashes are 64 hex characters. Nobody reads those; show enough to compare. */
function short(hash: string): string {
  if (!hash) return "—";
  return hash.length <= 20 ? hash : `${hash.slice(0, 10)}…${hash.slice(-6)}`;
}

function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

export default function AuditPage() {
  const [page, setPage] = useState<LedgerPage | null>(null);
  const [chain, setChain] = useState<ChainVerification | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const [entityId, setEntityId] = useState("");
  const [action, setAction] = useState("");

  const [paymentId, setPaymentId] = useState("");
  const [passport, setPassport] = useState<Passport | null>(null);
  const [verdict, setVerdict] = useState<PassportVerdict | null>(null);
  const [tampered, setTampered] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams({ limit: "50" });
      if (entityId.trim()) params.set("entity_id", entityId.trim());
      if (action.trim()) params.set("action", action.trim());
      setPage(await apiFetch<LedgerPage>(`/api/v1/audit/entries?${params}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read the ledger");
    }
  }, [entityId, action]);

  useEffect(() => {
    load();
  }, [load]);

  const verifyChain = async () => {
    setError("");
    try {
      setChain(await apiFetch<ChainVerification>("/api/v1/audit/verify"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not verify the chain");
    }
  };

  const issuePassport = async () => {
    const id = paymentId.trim();
    if (!id) return;
    setError("");
    setVerdict(null);
    setTampered(false);
    setBusy(true);
    try {
      setPassport(await apiFetch<Passport>(`/api/v1/passport/${encodeURIComponent(id)}`));
    } catch (e) {
      setPassport(null);
      setError(e instanceof Error ? e.message : "No passport for that payment");
    } finally {
      setBusy(false);
    }
  };

  /** Verify exactly what is on screen — including the tampered copy. */
  const verifyPassport = async (candidate: Passport) => {
    setError("");
    setBusy(true);
    try {
      setVerdict(
        await apiFetch<PassportVerdict>("/api/v1/passport/verify", {
          method: "POST",
          body: JSON.stringify({ body: candidate.body, signature: candidate.signature }),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verification failed to run");
    } finally {
      setBusy(false);
    }
  };

  /**
   * Alter the amount and re-submit. The signature is untouched, which is the
   * point: the tag no longer matches the body it was made for.
   */
  const tamper = async () => {
    if (!passport) return;
    const payment = (passport.body.payment ?? {}) as Record<string, unknown>;
    const current = Number(payment.amount_paise ?? 0);
    const forged: Passport = {
      ...passport,
      body: { ...passport.body, payment: { ...payment, amount_paise: current + 1 } },
    };
    setPassport(forged);
    setTampered(true);
    await verifyPassport(forged);
  };

  return (
    <div>
      <h1 style={{ marginBottom: 4 }}>Audit</h1>
      <p style={{ color: ui.mut, marginTop: 0 }}>
        Every money action is written to an append-only chain, and every payment can be handed
        to someone who does not trust this server.
      </p>
      {error && <Notice kind="bad">{error}</Notice>}

      <Card
        title="Chain integrity"
        right={<Button variant="ghost" onClick={verifyChain}>Verify chain</Button>}
      >
        {!chain ? (
          <p style={{ color: ui.mut, margin: 0, fontSize: 14 }}>
            Walks every entry and recomputes its hash from the entry before it.
          </p>
        ) : (
          <div>
            <p style={{ margin: "0 0 8px", fontSize: 15 }}>
              <strong style={{ color: chain.valid ? ui.ok : ui.bad }}>
                {chain.valid ? "Intact" : "Broken"}
              </strong>{" "}
              <span style={{ color: ui.mut }}>
                · {chain.entry_count} entries · {chain.merchant_id}
              </span>
            </p>
            <p style={{ ...mono, fontSize: 12, color: ui.mut, margin: 0, wordBreak: "break-all" }}>
              head {chain.head_hash || "—"}
            </p>
            {chain.errors.map((e) => (
              <Notice key={e} kind="bad">
                {e}
              </Notice>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Ledger"
        right={
          <span style={{ color: ui.mut, fontSize: 13 }}>
            {page ? `${page.entries.length} of ${page.total}` : ""}
          </span>
        }
      >
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <Field label="Entity id" value={entityId} onChange={setEntityId} placeholder="ord_… / pay_…" />
          </div>
          <div style={{ flex: 1 }}>
            <Field label="Action" value={action} onChange={setAction} placeholder="payment.captured" />
          </div>
          <Button variant="ghost" onClick={load} style={{ marginBottom: 10 }}>
            Filter
          </Button>
        </div>

        {!page || page.entries.length === 0 ? (
          <p style={{ color: ui.mut, fontSize: 14 }}>
            Nothing recorded yet. Run a purchase and it will appear here.
          </p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: ui.mut }}>
                <th style={{ padding: "6px 8px" }}>#</th>
                <th style={{ padding: "6px 8px" }}>When</th>
                <th style={{ padding: "6px 8px" }}>Actor</th>
                <th style={{ padding: "6px 8px" }}>Action</th>
                <th style={{ padding: "6px 8px" }}>Subject</th>
                <th style={{ padding: "6px 8px" }}>prev → entry</th>
              </tr>
            </thead>
            <tbody>
              {page.entries.map((entry) => (
                <tr
                  key={entry.entry_hash}
                  onClick={() => setExpanded(expanded === entry.seq ? null : entry.seq)}
                  style={{ borderTop: `1px solid ${ui.line}`, cursor: "pointer" }}
                >
                  <td style={{ padding: "6px 8px", ...mono }}>{entry.seq}</td>
                  <td style={{ padding: "6px 8px", color: ui.mut }}>{when(entry.recorded_at)}</td>
                  <td style={{ padding: "6px 8px" }}>{entry.actor}</td>
                  <td style={{ padding: "6px 8px", fontWeight: 600 }}>{entry.action}</td>
                  <td style={{ padding: "6px 8px", color: ui.mut }}>
                    {entry.entity_type}/{entry.entity_id}
                  </td>
                  <td style={{ padding: "6px 8px", ...mono, color: ui.mut }}>
                    {short(entry.prev_hash)} → {short(entry.entry_hash)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {page && expanded !== null && (
          <pre
            style={{
              ...mono,
              background: "#f8fafc",
              border: `1px solid ${ui.line}`,
              borderRadius: 8,
              padding: 12,
              marginTop: 12,
              fontSize: 12,
              overflowX: "auto",
            }}
          >
            {JSON.stringify(
              page.entries.find((e) => e.seq === expanded)?.payload ?? {},
              null,
              2,
            )}
          </pre>
        )}
      </Card>

      <Card title="Transaction passport">
        <p style={{ color: ui.mut, fontSize: 14, marginTop: 0 }}>
          A signed record of one payment. Verification checks the signature against the body — it
          never looks the payment up, so anyone can run it without access to this system.
        </p>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <Field label="Payment id" value={paymentId} onChange={setPaymentId} placeholder="pay_…" />
          </div>
          <Button onClick={issuePassport} disabled={busy} style={{ marginBottom: 10 }}>
            Issue
          </Button>
        </div>

        {passport && (
          <div>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <Button variant="ghost" onClick={() => verifyPassport(passport)} disabled={busy}>
                Verify
              </Button>
              <Button variant="danger" onClick={tamper} disabled={busy || tampered}>
                Tamper with the amount
              </Button>
            </div>

            {verdict && (
              <Notice kind={verdict.valid ? "ok" : "bad"}>
                {verdict.valid
                  ? "Signature holds — this passport is exactly what was signed."
                  : `Rejected: ${verdict.errors.join("; ") || "signature does not match the body"}`}
              </Notice>
            )}
            {tampered && (
              <Notice kind="info">
                One paisa was added to the body and nothing else was changed. Re-issue to get the
                genuine passport back.
              </Notice>
            )}

            <pre
              style={{
                ...mono,
                background: "#f8fafc",
                border: `1px solid ${tampered ? ui.bad : ui.line}`,
                borderRadius: 8,
                padding: 12,
                fontSize: 12,
                overflowX: "auto",
              }}
            >
              {JSON.stringify(passport, null, 2)}
            </pre>
          </div>
        )}
      </Card>
    </div>
  );
}
