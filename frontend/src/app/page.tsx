import Link from "next/link";

/**
 * The landing page. A server component on purpose — it needs no token, no
 * fetch and no state, so it ships as static HTML and is the fastest thing in
 * the product to load.
 *
 * It makes one claim and then shows the mechanism behind it, because the
 * interesting part of this system is not that an AI can buy things. It is
 * where the AI stops.
 */

const STEPS = [
  {
    step: "01",
    who: "The agent",
    title: "Shops",
    body: "Searches the catalogue, builds a cart, checks out to a pending order. Every price comes from the merchant's catalogue; nothing the agent sends can set one.",
  },
  {
    step: "02",
    who: "The agent",
    title: "Asks",
    body: "Requests authorization for that exact order and amount. It cannot approve its own request, and it holds no credential that moves money.",
  },
  {
    step: "03",
    who: "A human",
    title: "Decides",
    body: "Sees what policy ruled, what risk scored, and what is being bought. Signs off, or doesn't. The approval binds to these goods at this price.",
  },
  {
    step: "04",
    who: "The Control Plane",
    title: "Pays",
    body: "Spends the approval once, takes the amount from the order, and writes the whole thing — including who permitted it — to an append-only chain.",
  },
];

const GUARANTEES = [
  {
    title: "Bounded",
    body: "Every money action is judged against explicit limits before it happens: per-payment ceilings, daily caps, velocity, and a campaign budget that cannot be overspent under any amount of concurrency.",
  },
  {
    title: "Gated",
    body: "Permission is single-use, time-bound, and fingerprinted to the kind, the amount and the goods. An approval for one order cannot be presented against another, and one approval can never pay for two charges.",
  },
  {
    title: "Explainable",
    body: "A hash-chained entry is written for every step, the refusals included. Any payment can be issued as a signed passport that verifies without trusting this server at all.",
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-white">
      <header className="border-b border-slate-200">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <span className="text-lg font-semibold tracking-tight text-slate-900">KeenPay</span>
          <Link
            href="/login"
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
          >
            Sign in
          </Link>
        </div>
      </header>

      <section className="border-b border-slate-200 bg-gradient-to-b from-brand-50 to-white">
        <div className="mx-auto max-w-6xl px-6 py-20 sm:py-28">
          <p className="mb-4 inline-flex rounded-full bg-white px-3 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
            Secure AI Payments
          </p>
          <h1 className="max-w-3xl text-4xl font-semibold leading-tight tracking-tight text-slate-900 sm:text-6xl">
            Let AI buy from you.
            <br />
            Don&apos;t let it move the money.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-slate-600">
            KeenPay lets autonomous buyers discover, negotiate and check out against your
            catalogue — while every rupee that actually moves passes through a deterministic
            Control Plane a human still owns.
          </p>
          <div className="mt-10 flex flex-wrap items-center gap-3">
            <Link
              href="/login?as=buyer"
              className="rounded-lg bg-brand-600 px-5 py-3 text-sm font-semibold text-white shadow-lift transition-colors hover:bg-brand-700"
            >
              I&apos;m a buyer
            </Link>
            <Link
              href="/login?as=merchant"
              className="rounded-lg border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition-colors hover:bg-slate-50"
            >
              I&apos;m a merchant
            </Link>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-20">
        <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Where the AI stops</h2>
        <p className="mt-3 max-w-2xl text-slate-600">
          The agent reasons and recommends. Between its last action and money leaving an account
          there is a refusal and a human. Remove either one and the purchase does not complete.
        </p>
        <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <li key={s.step} className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-xs text-brand-600">{s.step}</span>
                <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  {s.who}
                </span>
              </div>
              <h3 className="mt-3 text-lg font-semibold text-slate-900">{s.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-y border-slate-200 bg-slate-50">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
            Three things that are true of every money action
          </h2>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {GUARANTEES.map((g) => (
              <div
                key={g.title}
                className="rounded-xl border border-slate-200 bg-white p-6 shadow-card"
              >
                <h3 className="text-lg font-semibold text-slate-900">{g.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-slate-600">{g.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="rounded-2xl bg-slate-900 px-8 py-12 sm:px-12">
          <h2 className="max-w-2xl text-3xl font-semibold tracking-tight text-white">
            Prove it rather than believe it.
          </h2>
          <p className="mt-4 max-w-2xl leading-relaxed text-slate-300">
            Sign in and issue a passport for a real payment, then change one paisa in it. The same
            verifier that just accepted the document refuses the altered one.
          </p>
          <Link
            href="/login"
            className="mt-8 inline-flex rounded-lg bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition-colors hover:bg-slate-100"
          >
            Open the console
          </Link>
        </div>
      </section>

      <footer className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-8 text-sm text-slate-500">
          KeenPay — the AI reasons and recommends; the Control Plane owns money.
        </div>
      </footer>
    </main>
  );
}
