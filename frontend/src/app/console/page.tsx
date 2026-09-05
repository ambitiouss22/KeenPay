"use client";

/**
 * The dashboard, which is a different product for the two sides.
 *
 * A buyer sees what they can do with the merchant's catalogue. A merchant sees
 * the controls over their own money. Neither list is written here — both come
 * from the capability map, so adding a feature in one place puts it in the nav,
 * on this page, and nowhere it does not belong.
 */

import Link from "next/link";
import { useAuth } from "@/hooks/useAuth";
import { audienceFor, featuresFor } from "@/lib/roles";

const INTRO = {
  buyer: {
    title: "Your shopping console",
    body: "Browse, build a cart and check out. Checking out creates a pending order — it is not a charge, and it does not become one until someone on the merchant's side approves this exact amount for these exact goods.",
  },
  merchant: {
    title: "Your merchant console",
    body: "Agents can discover and buy from your catalogue on their own. Nothing they do moves money until you approve it, and every attempt — the refused ones included — is written to a chain you can hand to an auditor.",
  },
} as const;

export default function ConsoleHome() {
  const { user } = useAuth();
  const role = user?.role;
  const audience = audienceFor(role);
  const features = featuresFor(role);
  const intro = INTRO[audience];

  return (
    <div>
      <section className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{intro.title}</h1>
        <p className="mt-2 max-w-3xl leading-relaxed text-slate-600">{intro.body}</p>
      </section>

      {features.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <h2 className="text-base font-semibold text-slate-900">Nothing is open to this account</h2>
          <p className="mt-2 text-sm text-slate-600">
            The role <span className="font-mono">{role ?? "unknown"}</span> has no console screens.
            That is the right answer for service and machine accounts, which talk to the API
            directly rather than through a browser.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((feature) => (
            <Link
              key={feature.href}
              href={feature.href}
              className="group rounded-xl border border-slate-200 bg-white p-5 shadow-card transition-shadow hover:shadow-lift"
            >
              <h2 className="text-base font-semibold text-slate-900 group-hover:text-brand-700">
                {feature.title}
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{feature.blurb}</p>
              <span aria-hidden className="mt-4 inline-block text-sm font-semibold text-brand-600">
                Open &rarr;
              </span>
            </Link>
          ))}
        </div>
      )}

      <p className="mt-8 text-sm text-slate-500">
        Signed in as <span className="font-mono">{role}</span>. This console shows only what your
        account may actually do — the API refuses the rest regardless of what a page draws.
      </p>
    </div>
  );
}
