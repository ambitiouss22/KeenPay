"use client";

/**
 * The shared pieces every console screen is built from.
 *
 * The exported names and their props are unchanged from the inline-styled
 * version this replaces, which is why restyling this one file restyles every
 * page that imports it. `ui` survives for the same reason: several screens set
 * a colour inline, and keeping the object — with values that match the Tailwind
 * palette exactly — means those screens stay coherent instead of drifting a
 * shade away from everything else.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";

/** The palette, for the few places that still set a colour directly. */
export const ui = {
  ink: "#2E2C26", // slate-900
  panel: "#ffffff",
  line: "#E6E1D6", // slate-200
  mut: "#7C776C", // slate-500
  accent: "#578266", // brand-600
  ok: "#4C8461", // ok-600
  bad: "#AE6151", // stop-600
};

export function Card({
  title,
  right,
  children,
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-4 rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="m-0 text-base font-semibold text-slate-900">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

const VARIANTS = {
  primary: "border-transparent bg-brand-600 text-white hover:bg-brand-700",
  ghost: "border-slate-200 bg-white text-slate-900 hover:bg-slate-50",
  danger: "border-transparent bg-stop-600 text-white hover:bg-stop-700",
} as const;

export function Button({
  children,
  variant = "primary",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS;
}) {
  return (
    <button
      className={[
        "inline-flex items-center justify-center gap-2 rounded-lg border px-4 py-2",
        "text-sm font-semibold transition-colors disabled:cursor-default disabled:opacity-50",
        VARIANTS[variant],
        className,
      ].join(" ")}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="mb-3 block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
      />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

const NOTICE = {
  ok: "border-ok-200 bg-ok-50 text-ok-700",
  bad: "border-stop-200 bg-stop-50 text-stop-700",
  info: "border-slate-200 bg-slate-50 text-slate-600",
} as const;

export function Notice({ kind, children }: { kind: keyof typeof NOTICE; children: ReactNode }) {
  return <p className={`my-2 rounded-lg border px-3 py-2 text-sm ${NOTICE[kind]}`}>{children}</p>;
}

const TONES = {
  neutral: "bg-slate-100 text-slate-700",
  ok: "bg-ok-50 text-ok-700 ring-1 ring-inset ring-ok-200",
  stop: "bg-stop-50 text-stop-700 ring-1 ring-inset ring-stop-200",
  brand: "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200",
} as const;

/**
 * A short label for a state a reader scans for: a status, a verdict, a role.
 * Deliberately not a colour-only signal — the word carries the meaning and the
 * colour only reinforces it.
 */
export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: keyof typeof TONES;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}
