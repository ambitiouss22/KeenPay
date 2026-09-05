"use client";

/**
 * Sign in as a buyer or as a merchant.
 *
 * The chooser is a convenience, not a permission. It decides which account is
 * pre-filled and how the page describes itself; where you land is decided by
 * the role inside the token the API returns. Pick "Merchant", sign in with a
 * shopper account, and you get the buyer console — because an authorization
 * decision made in a browser is no decision at all, and the API would refuse
 * every merchant call anyway.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import type { Audience } from "@/lib/roles";

const PRESETS: Record<
  Audience,
  { email: string; headline: string; sub: string; points: string[] }
> = {
  buyer: {
    email: "shopper@keenpay.dev",
    headline: "Shop with an agent working for you",
    sub: "Browse the catalogue, build a cart and check out. Nothing you send can set a price, and no order becomes a charge without a human on the merchant's side agreeing to it.",
    points: [
      "Prices come from the merchant's catalogue",
      "Checkout creates a pending order, never a charge",
      "Every order you place is on the audit trail",
    ],
  },
  merchant: {
    email: "manager@keenpay.dev",
    headline: "Sell to AI buyers without handing over the till",
    sub: "Approve the money movements agents ask for, cap what growth campaigns can spend, and read the chain that records all of it.",
    points: [
      "Approve or refuse each money movement yourself",
      "Campaign budgets that cannot be overspent",
      "A signed passport for any payment, verifiable offline",
    ],
  },
};

const PASSWORD = "KeenPayDev1!";

function LoginForm() {
  const { user, loading, login } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  const requested = params.get("as");
  const initial: Audience = requested === "buyer" ? "buyer" : "merchant";

  const [audience, setAudience] = useState<Audience>(initial);
  const [email, setEmail] = useState(PRESETS[initial].email);
  const [password, setPassword] = useState(PASSWORD);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Already signed in? There is nothing to do here.
  useEffect(() => {
    if (!loading && user) router.replace("/console");
  }, [loading, user, router]);

  const choose = (next: Audience) => {
    setAudience(next);
    setError("");
    // Only swap the address if it is still a preset. Someone who typed their
    // own must not have it wiped by clicking the other tab.
    setEmail((current) =>
      current === PRESETS.buyer.email || current === PRESETS.merchant.email
        ? PRESETS[next].email
        : current,
    );
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      router.push("/console");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  const preset = PRESETS[audience];

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      {/* --- the pitch, for the side they picked --- */}
      <section className="hidden bg-slate-900 px-12 py-16 lg:flex lg:flex-col lg:justify-between">
        <Link href="/" className="text-lg font-semibold tracking-tight text-white">
          KeenPay
        </Link>
        <div className="animate-rise" key={audience}>
          <h2 className="max-w-md text-3xl font-semibold leading-tight tracking-tight text-white">
            {preset.headline}
          </h2>
          <p className="mt-5 max-w-md leading-relaxed text-slate-300">{preset.sub}</p>
          <ul className="mt-8 space-y-3">
            {preset.points.map((point) => (
              <li key={point} className="flex gap-3 text-sm text-slate-300">
                <span aria-hidden className="text-brand-500">
                  &#10003;
                </span>
                {point}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-sm text-slate-500">
          The AI reasons and recommends; the Control Plane owns money.
        </p>
      </section>

      {/* --- the form --- */}
      <section className="flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-sm">
          <Link
            href="/"
            className="mb-8 inline-block text-lg font-semibold tracking-tight text-slate-900 lg:hidden"
          >
            KeenPay
          </Link>

          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h1>
          <p className="mt-2 text-sm text-slate-600">
            Choose how you use KeenPay. What you can actually do is set by your account, not by
            this choice.
          </p>

          <div
            role="tablist"
            aria-label="Sign in as"
            className="mt-6 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1"
          >
            {(["buyer", "merchant"] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={audience === option}
                onClick={() => choose(option)}
                className={[
                  "rounded-lg px-3 py-2 text-sm font-semibold capitalize transition-colors",
                  audience === option
                    ? "bg-white text-slate-900 shadow-card"
                    : "text-slate-500 hover:text-slate-900",
                ].join(" ")}
              >
                {option}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="mt-6">
            <label className="mb-4 block">
              <span className="mb-1 block text-xs font-medium text-slate-600">Email</span>
              <input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </label>
            <label className="mb-4 block">
              <span className="mb-1 block text-xs font-medium text-slate-600">Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </label>

            {error && (
              <p className="mb-4 rounded-lg border border-stop-200 bg-stop-50 px-3 py-2 text-sm text-stop-700">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy || loading}
              className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-default disabled:opacity-50"
            >
              {busy ? "Signing in…" : `Continue as ${audience}`}
            </button>
          </form>

          <div className="mt-8 rounded-xl border border-slate-200 bg-slate-50 p-4">
            <p className="text-xs font-medium text-slate-600">Development accounts</p>
            <ul className="mt-2 space-y-1 text-xs text-slate-500">
              <li>
                <span className="font-mono">shopper@keenpay.dev</span> — buyer
              </li>
              <li>
                <span className="font-mono">manager@keenpay.dev</span> — approves money movement
              </li>
              <li>
                <span className="font-mono">admin@keenpay.dev</span> — everything, catalogue
                included
              </li>
            </ul>
            <p className="mt-2 text-xs text-slate-500">
              Password <span className="font-mono">{PASSWORD}</span> for all three.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  // useSearchParams needs a boundary, or the whole route opts out of static
  // rendering at build time.
  return (
    <Suspense fallback={<main className="min-h-screen bg-white" />}>
      <LoginForm />
    </Suspense>
  );
}
