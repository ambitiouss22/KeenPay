"use client";

/**
 * The console shell.
 *
 * A left rail rather than a top bar, and the rail is grouped rather than a
 * flat list. The grouping is the product's own shape: what you sell
 * (Commerce), what you do to sell more of it (Growth), and what stands between
 * an AI and the money (Control). A person looking for the audit trail should
 * not have to remember whether it was filed under reporting or settings — it
 * is under Control, with the approvals, because those are the same job.
 *
 * The items come from the signed-in role, so a buyer is never shown Growth and
 * a manager is never shown a Catalogue link that would answer 403. That is a
 * rendering decision only; the API refuses on its own regardless of what the
 * browser drew.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { useAuth } from "@/hooks/useAuth";
import type { Audience, Feature } from "@/lib/roles";
import { audienceFor, audienceLabel, featuresFor } from "@/lib/roles";

/**
 * Written out in full because Tailwind reads the source as text. A class
 * assembled at runtime (`bg-${tint}-100`) is invisible to the scanner and gets
 * stripped from the stylesheet, so the badge would render unstyled.
 */
const MARK: Record<Audience, string> = {
  buyer: "bg-buyer-100 text-buyer-700",
  merchant: "bg-merchant-100 text-merchant-700",
};

/**
 * Which section of the rail each screen belongs under.
 *
 * Kept here rather than in `lib/roles.ts` on purpose: it is a navigation
 * concern, and that file already carries the one thing that must not drift
 * from the API — who may see what.
 */
const GROUPS: { label: string; hrefs: string[] }[] = [
  {
    label: "Commerce",
    hrefs: ["/console/chat", "/console/shop", "/console/products", "/console/orders"],
  },
  { label: "Growth", hrefs: ["/console/campaigns", "/console/buy"] },
  { label: "Control", hrefs: ["/console/approvals", "/console/audit"] },
];

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        {loading ? "Loading…" : "Redirecting to sign in…"}
      </div>
    );
  }

  const audience = audienceFor(user.role);
  const nav = featuresFor(user.role);
  const byHref = new Map(nav.map((item) => [item.href, item]));

  // Only sections this role can actually open. An empty heading is worse than
  // a missing one: it reads as something broken rather than something absent.
  const sections = GROUPS.map((group) => {
    const items: Feature[] = [];
    for (const href of group.hrefs) {
      const item = byHref.get(href);
      if (item) items.push(item);
    }
    return { label: group.label, items };
  }).filter((section) => section.items.length > 0);

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <span
            className={`flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold ${MARK[audience]}`}
            aria-hidden
          >
            K
          </span>
          <span className="leading-tight">
            <Link href="/console" className="block text-sm font-semibold tracking-tight">
              KeenPay
            </Link>
            <span className="block text-[10px] uppercase tracking-[0.14em] text-slate-400">
              {audienceLabel(audience)} console
            </span>
          </span>
        </div>

        <nav className="flex-1 px-3 pb-6">
          {sections.map((section) => (
            <div key={section.label} className="mb-5">
              <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                {section.label}
              </p>
              {section.items.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={[
                      "relative mb-0.5 block rounded-lg px-3 py-2 text-sm transition-colors",
                      active
                        ? "bg-brand-50 font-semibold text-brand-800"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                    ].join(" ")}
                  >
                    {active && (
                      <span
                        className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r bg-brand-500"
                        aria-hidden
                      />
                    )}
                    {item.title}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="border-t border-slate-200 px-5 py-4">
          <p className="truncate text-xs font-medium text-slate-700">{user.email}</p>
          <p className="text-xs text-slate-400">{user.role}</p>
          <button
            onClick={async () => {
              await logout();
              router.replace("/login");
            }}
            className="mt-2.5 w-full rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* The rail is hidden below lg, so small screens keep a usable menu. */}
        <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur lg:hidden">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <Link href="/console" className="text-sm font-semibold tracking-tight">
              KeenPay
            </Link>
            <nav className="flex flex-wrap gap-1">
              {nav.map((item) => {
                const active = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={[
                      "rounded-lg px-2.5 py-1 text-xs transition-colors",
                      active
                        ? "bg-brand-600 font-semibold text-white"
                        : "text-slate-600 hover:bg-slate-100",
                    ].join(" ")}
                  >
                    {item.title}
                  </Link>
                );
              })}
            </nav>
            <button
              onClick={async () => {
                await logout();
                router.replace("/login");
              }}
              className="ml-auto rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold"
            >
              Sign out
            </button>
          </div>
        </header>

        <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
