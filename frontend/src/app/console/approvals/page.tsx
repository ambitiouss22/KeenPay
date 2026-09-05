"use client";

/**
 * The human in the loop.
 *
 * This is the screen the whole architecture exists to make possible: an agent
 * has asked for money to move, and a person decides. So it shows what was
 * actually judged — the policy verdict with its reasons, the risk band, how
 * many approvals are required and who has already given one — rather than a
 * summary and an Approve button. An approver who cannot see why they are being
 * asked is a rubber stamp with a job title.
 *
 * Two refusals are enforced by the API and stated here so the page does not
 * imply otherwise: the requester cannot approve their own request, and one
 * person cannot fill a two-person quorum by clicking twice.
 */

import { useState } from "react";
import { Notice, Pill } from "@/components/ui/kit";
import { apiFetch } from "@/lib/api-client";
import { rupees } from "@/lib/format";

interface Approver {
  approver_id?: string;
  role?: string;
  at?: string;
}

interface Authorization {
  id: string;
  action_kind: string;
  amount_paise: number;
  currency: string;
  subject_id: string;
  requested_by: string;
  requested_by_role: string;
  status: string;
  required_approvals: number;
  approvers: Approver[];
  reasons: string[];
  policy_decision: Record<string, unknown>;
  risk: Record<string, unknown>;
  expires_at?: string | null;
}

const TONE: Record<string, "ok" | "stop" | "brand" | "neutral"> = {
  approved: "ok",
  consumed: "neutral",
  pending: "brand",
  denied: "stop",
  revoked: "stop",
  expired: "stop",
};

function when(value?: string | null): string {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleString();
}

export default function ApprovalsPage() {
  const [id, setId] = useState("");
  const [note, setNote] = useState("");
  const [record, setRecord] = useState<Authorization | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async (event?: React.FormEvent) => {
    event?.preventDefault();
    const value = id.trim();
    if (!value) return;
    setError("");
    setBusy(true);
    try {
      setRecord(
        await apiFetch<Authorization>(`/api/v1/authorizations/${encodeURIComponent(value)}`),
      );
    } catch (e) {
      setRecord(null);
      setError(e instanceof Error ? e.message : "No authorization with that id");
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!record) return;
    setError("");
    setBusy(true);
    try {
      setRecord(
        await apiFetch<Authorization>(`/api/v1/authorizations/${record.id}/approve`, {
          method: "POST",
          body: JSON.stringify({ note: note.trim() || null }),
        }),
      );
      setNote("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "The approval was refused");
    } finally {
      setBusy(false);
    }
  };

  const outstanding = record
    ? Math.max(0, record.required_approvals - record.approvers.length)
    : 0;

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Approvals</h1>
      <p className="mt-2 max-w-3xl text-slate-600">
        An agent asked for money to move. Read what was judged, then decide. Your approval binds to
        this amount and these goods — it cannot be spent on anything else, and it can be spent
        once.
      </p>

      <form onSubmit={load} className="mt-6 flex max-w-xl gap-2">
        <input
          value={id}
          onChange={(e) => setId(e.target.value)}
          placeholder="auth_…"
          className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50"
        >
          {busy ? "Loading…" : "Open"}
        </button>
      </form>

      {error && <Notice kind="bad">{error}</Notice>}

      {record && (
        <div className="mt-6 animate-rise space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-sm text-slate-500">
                  {record.requested_by_role} <span className="font-mono">{record.requested_by}</span>{" "}
                  wants to {record.action_kind}
                </p>
                <p className="mt-1 text-3xl font-semibold text-slate-900">
                  {rupees(record.amount_paise)}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  for <span className="font-mono">{record.subject_id}</span>
                </p>
              </div>
              <Pill tone={TONE[record.status] ?? "neutral"}>{record.status}</Pill>
            </div>

            <dl className="mt-6 grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-slate-500">Approvals required</dt>
                <dd className="text-slate-900">
                  {record.approvers.length} of {record.required_approvals}
                  {outstanding > 0 && (
                    <span className="text-slate-500"> · {outstanding} outstanding</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Risk band</dt>
                <dd className="text-slate-900">{String(record.risk?.band ?? "—")}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Expires</dt>
                <dd className="text-slate-900">{when(record.expires_at)}</dd>
              </div>
            </dl>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
            <h2 className="text-base font-semibold text-slate-900">Why you are being asked</h2>
            {record.reasons.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No rule raised an objection; the escalation came from who is asking or from the
                risk band.
              </p>
            ) : (
              <ul className="mt-3 space-y-2">
                {record.reasons.map((reason) => (
                  <li
                    key={reason}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
                  >
                    {reason}
                  </li>
                ))}
              </ul>
            )}

            <h3 className="mt-6 text-sm font-semibold text-slate-900">The full decision</h3>
            <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-700">
              {JSON.stringify({ policy: record.policy_decision, risk: record.risk }, null, 2)}
            </pre>
          </div>

          {record.approvers.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
              <h2 className="text-base font-semibold text-slate-900">Who has signed off</h2>
              <ul className="mt-3 divide-y divide-slate-100">
                {record.approvers.map((approver, index) => (
                  <li
                    key={`${approver.approver_id ?? "approver"}-${index}`}
                    className="flex justify-between gap-3 py-2 text-sm"
                  >
                    <span className="font-mono text-slate-700">{approver.approver_id}</span>
                    <span className="text-slate-500">
                      {approver.role} · {when(approver.at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {record.status === "pending" ? (
            <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
              <h2 className="text-base font-semibold text-slate-900">Your decision</h2>
              <label className="mt-3 block">
                <span className="mb-1 block text-xs font-medium text-slate-600">
                  Note (stored with your approval, never parsed)
                </span>
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Reviewed the cart and the price"
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
                />
              </label>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  onClick={approve}
                  disabled={busy}
                  className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
                >
                  {busy ? "Recording…" : "Approve"}
                </button>
                <button
                  onClick={() => load()}
                  disabled={busy}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50"
                >
                  Refresh
                </button>
                <span className="text-xs text-slate-500">
                  Doing nothing is also a decision — an unapproved authorization lapses at its
                  expiry and can never be spent.
                </span>
              </div>
            </div>
          ) : (
            <Notice kind={record.status === "approved" ? "ok" : "info"}>
              {record.status === "approved"
                ? "Approved and spendable — once, on this amount and these goods."
                : `This authorization is ${record.status}; there is nothing left to decide.`}
            </Notice>
          )}
        </div>
      )}
    </div>
  );
}
