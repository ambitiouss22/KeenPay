"use client";

/**
 * Talk to the agent.
 *
 * The session is opened on the Control Plane the moment this page loads, so
 * every message belongs to a session that exists, is scoped to this merchant,
 * and is on the audit trail. The chat cannot invent one client-side.
 *
 * What the agent says here is a *recommendation*. It can search the catalogue
 * and suggest a cart; it cannot price anything and it cannot pay. That is not
 * a rule this page enforces — the API refuses regardless — but the page says
 * so plainly, because a buyer should know which parts of the answer are the
 * store's and which are the model's.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";

interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
}

const SUGGESTIONS = [
  "2 navy hoodies in medium, best price",
  "Something under ₹1,000",
  "What do you have in stock?",
];

export default function ChatPage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiFetch<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ merchant_id: "merchant_keen" }),
    })
      .then((s) => setSessionId(s.session_id))
      .catch((e) => setError(e instanceof Error ? e.message : "Could not open a session"));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  const send = useCallback(
    async (text: string) => {
      const body = text.trim();
      if (!body || !sessionId || busy) return;

      setInput("");
      setError("");
      setTurns((t) => [...t, { id: `u-${Date.now()}`, role: "user", text: body }]);
      setBusy(true);
      try {
        const reply = await apiFetch<{ message_id: string; role: string; text: string }>(
          `/api/v1/sessions/${sessionId}/messages`,
          { method: "POST", body: JSON.stringify({ text: body }) },
        );
        setTurns((t) => [
          ...t,
          { id: reply.message_id, role: "assistant", text: reply.text },
        ]);
      } catch (e) {
        setError(e instanceof Error ? e.message : "The agent did not answer");
      } finally {
        setBusy(false);
      }
    },
    [sessionId, busy],
  );

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Chat</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
        Ask for what you want in your own words. The agent searches the merchant&apos;s
        catalogue and recommends — it never sets a price, and it cannot pay for anything.
      </p>

      <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-card">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <span className="text-sm font-semibold">KeenPay shopping agent</span>
          <span className="font-mono text-xs text-slate-400">
            {sessionId ? `session ${sessionId.slice(0, 8)}…` : "opening session…"}
          </span>
        </div>

        <div className="h-[380px] space-y-3 overflow-y-auto bg-slate-50 px-5 py-4">
          {turns.length === 0 && !busy && (
            <p className="text-sm text-slate-400">
              Nothing said yet. Try one of the suggestions below.
            </p>
          )}
          {turns.map((turn) => (
            <div
              key={turn.id}
              className={turn.role === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <p
                className={[
                  "max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                  turn.role === "user"
                    ? "bg-buyer-600 text-white"
                    : "border border-slate-200 bg-white text-slate-800",
                ].join(" ")}
              >
                {turn.text}
              </p>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <p className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-400">
                thinking…
              </p>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t border-slate-200 px-5 py-4">
          {error && <p className="mb-2 text-sm text-stop-700">{error}</p>}
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send(input);
              }}
              disabled={!sessionId}
              placeholder="Type what you are looking for…"
              className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm placeholder:text-slate-400"
            />
            <button
              onClick={() => send(input)}
              disabled={busy || !sessionId || !input.trim()}
              className="rounded-xl bg-brand-700 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-800 disabled:opacity-40"
            >
              Ask
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                disabled={busy || !sessionId}
                className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-40"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="mt-4 text-sm text-slate-500">
        Ready to buy?{" "}
        <Link href="/console/shop" className="font-semibold text-brand-700 hover:underline">
          Open the catalogue
        </Link>{" "}
        — the cart and checkout live there, and checking out creates a pending order, never a
        charge.
      </p>
    </div>
  );
}
