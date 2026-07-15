import type { MetaFunction } from "react-router";
import { Link } from "react-router";
import {
  HiClipboardList,
  HiDatabase,
  HiDocumentSearch,
  HiLightningBolt,
  HiLockClosed,
  HiShieldCheck,
} from "react-icons/hi";
import { AppHeader } from "../components/AppHeader";
import { RequireAuth } from "../components/RequireAuth";

export const meta: MetaFunction = () => [
  { title: "Waypoint" },
  {
    name: "description",
    content: "Contract manufacturing supplier oversight workspace",
  },
];

const capabilities = [
  {
    title: "Teams handoff",
    description:
      "Start in Teams, then open Waypoint with the supplier invoice, citations, and investigation context.",
    icon: HiDocumentSearch,
    to: "/invoices",
  },
  {
    title: "Supplier invoice decisions",
    description:
      "Review invoice findings against purchase orders, MSAs, batch records, quality logs, and IP-sensitive process terms.",
    icon: HiLightningBolt,
    to: "/invoices",
  },
  {
    title: "Controlled escalation",
    description:
      "Stage finance, procurement, legal, and quality review packets before supplier outreach or payment action.",
    icon: HiLockClosed,
    to: "/agent",
  },
];

export default function Home() {
  return (
    <RequireAuth>
      <div className="min-h-screen bg-slate-50 text-slate-950">
        <AppHeader />

      <main
        id="main-content"
        className="mx-auto max-w-[1500px] px-3 py-4 2xl:px-4"
      >
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_400px]">
            <div>
              <div className="inline-flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1 text-sm font-medium text-blue-800">
                <HiDatabase className="h-4 w-4" />
                Caldova contract manufacturing workspace
              </div>
              <h1 className="mt-3 max-w-4xl text-3xl font-semibold tracking-tight">
                Waypoint helps Caldova investigate supplier invoices,
                protect IP-sensitive manufacturing commitments, and control
                escalation of supplier-caused quality deviations.
              </h1>
              <p className="mt-3 max-w-3xl text-base leading-7 text-slate-600">
                Start the question in Teams or Copilot, then use Waypoint to
                inspect invoice evidence, supplier behavior, batch records,
                quality logs, and agent decisions before finance, procurement,
                legal, or quality teams act.
              </p>

              <div className="mt-5 flex flex-wrap gap-2">
                <Link
                  to="/invoices"
                  className="inline-flex min-h-9 items-center gap-2 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700"
                >
                  Review invoices
                </Link>
                <Link
                  to="/agent"
                  className="inline-flex min-h-9 items-center gap-2 rounded-md border border-slate-200 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                >
                  View agent run
                </Link>
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-start gap-2.5">
                <div className="rounded-md bg-emerald-100 p-2 text-emerald-700">
                  <HiShieldCheck className="h-6 w-6" />
                </div>
                <div>
                  <h3 className="text-base font-semibold">Local dev mode</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    Authentication is bypassed locally. In a deployed
                    environment, route guards can redirect users to the Waypoint
                    login page before investigation routes load.
                  </p>
                </div>
              </div>

              <div className="mt-3 rounded-md bg-white p-3 shadow-sm">
                <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
                  Suggested next wiring
                </p>
                <ul className="mt-2 space-y-2 text-sm text-slate-600">
                  <li className="flex gap-2">
                    <HiClipboardList className="mt-0.5 h-4 w-4 text-blue-600" />
                    Add auth state to the root loader.
                  </li>
                  <li className="flex gap-2">
                    <HiClipboardList className="mt-0.5 h-4 w-4 text-blue-600" />
                    Gate protected routes outside `env.DEV`.
                  </li>
                  <li className="flex gap-2">
                    <HiClipboardList className="mt-0.5 h-4 w-4 text-blue-600" />
                    Replace mock rows and agent steps with API responses.
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        <section className="mt-3 grid gap-2 lg:grid-cols-3" aria-labelledby="principles-heading">
          <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm lg:col-span-1">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-700">
              Design posture
            </p>
            <h2 id="principles-heading" className="mt-1 text-lg font-semibold">
              Built for trust under data pressure
            </h2>
          </div>
          <div className="grid gap-2 lg:col-span-2 md:grid-cols-3">
            <Principle title="Show the work" detail="Expose sources, intermediate steps, and why an action is recommended." />
            <Principle title="Keep control visible" detail="Make approval boundaries explicit before agents publish or mutate data." />
            <Principle title="Reduce review load" detail="Prioritize high-impact exceptions while keeping drill-down paths available." />
          </div>
        </section>

        <section className="mt-3 grid gap-2 md:grid-cols-3">
          {capabilities.map((capability) => (
            <Link
              key={capability.title}
              to={capability.to}
              className="group rounded-lg border border-slate-200 bg-white p-3 shadow-sm transition hover:border-blue-200 hover:shadow-md"
            >
              <capability.icon className="h-7 w-7 text-blue-600" />
              <h3 className="mt-3 text-base font-semibold">{capability.title}</h3>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                {capability.description}
              </p>
              <span className="mt-3 inline-flex text-sm font-semibold text-blue-700 group-hover:text-blue-800">
                Open
              </span>
            </Link>
          ))}
        </section>
      </main>
      </div>
    </RequireAuth>
  );
}

function Principle({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <h3 className="font-semibold">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-600">{detail}</p>
    </div>
  );
}
