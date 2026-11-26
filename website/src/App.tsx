import { useState } from 'react';
import type { ReactNode } from 'react';
import { Check, Play, Terminal, Wrench } from 'lucide-react';
import {
  COMPETITIVE_MATRIX,
  DEMO_SCENARIOS,
  FALLBACK_TOOLS,
  MOCK_FUNDS,
  ONE_HOUR_TEST_DATA,
  PERSONAS,
  TUTORIAL_LABS,
} from './data/qaosData';
import type { DemoScenario } from './data/qaosData';

interface CallResult {
  duration_ms: number;
  is_error: boolean;
  result: Record<string, unknown>;
  source: 'live' | 'recorded';
}

const NAV = [
  ['demo', 'Live demo'],
  ['one-hour', 'One-hour test'],
  ['tools', 'Tools'],
  ['tutorial', 'Tutorial'],
  ['proof', 'Proof'],
] as const;

function Section({ id, title, lead, children }: { id: string; title: string; lead: string; children: ReactNode }) {
  return (
    <section id={id} className="mx-auto max-w-6xl px-6 py-20">
      <h2 className="text-3xl font-semibold tracking-tight">{title}</h2>
      <p className="mt-3 max-w-3xl text-slate-400">{lead}</p>
      <div className="mt-10">{children}</div>
    </section>
  );
}

function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs text-emerald-300">
      {children}
    </span>
  );
}

function Demo() {
  const [active, setActive] = useState<DemoScenario>(DEMO_SCENARIOS[0]);
  const [out, setOut] = useState<CallResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(scenario: DemoScenario) {
    setActive(scenario);
    setBusy(true);
    try {
      const res = await fetch('/demo-api/call', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          persona: scenario.persona,
          tool: scenario.tool,
          arguments: scenario.arguments,
        }),
      });
      if (!res.ok) throw new Error(`bridge returned ${res.status}`);
      const body = (await res.json()) as Omit<CallResult, 'source'>;
      setOut({ ...body, source: 'live' });
    } catch {
      setOut({ ...scenario.fallbackResponse, source: 'recorded' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      <ul className="space-y-2">
        {DEMO_SCENARIOS.map((s) => (
          <li key={s.id}>
            <button
              onClick={() => {
                setActive(s);
                setOut(null);
              }}
              className={`w-full rounded-lg border px-4 py-3 text-left transition ${
                s.id === active.id
                  ? 'border-emerald-500/60 bg-emerald-500/10'
                  : 'border-white/10 hover:border-white/25'
              }`}
            >
              <div className="text-sm font-medium">{s.label}</div>
              <div className="mt-1 text-xs text-slate-500">{s.badge}</div>
            </button>
          </li>
        ))}
      </ul>
      <div className="rounded-xl border border-white/10 bg-black/30 p-6">
        <p className="text-sm text-slate-400">Asked by {active.persona}:</p>
        <p className="mt-1 text-lg">“{active.prompt}”</p>
        <p className="mt-3 text-sm text-slate-400">{active.summary}</p>
        <pre className="mt-4 overflow-x-auto rounded-lg bg-black/40 p-4 text-xs text-slate-300">
          {active.tool}({JSON.stringify(active.arguments, null, 2)})
        </pre>
        <button
          onClick={() => void run(active)}
          disabled={busy}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
        >
          <Play size={16} /> {busy ? 'Calling…' : 'Call tool'}
        </button>
        {out && (
          <div className="mt-6">
            <div className="mb-2 flex items-center gap-3 text-xs text-slate-400">
              <Pill>{out.source === 'live' ? 'live MCP call' : 'recorded response'}</Pill>
              <span>{out.duration_ms} ms</span>
              <span className={out.is_error ? 'text-amber-300' : 'text-emerald-300'}>
                {out.is_error ? 'refused / error' : 'ok'}
              </span>
            </div>
            <pre className="max-h-96 overflow-auto rounded-lg bg-black/40 p-4 text-xs text-slate-300">
              {JSON.stringify(out.result, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function OneHourTest() {
  const d = ONE_HOUR_TEST_DATA;
  const th = 'px-3 py-2 text-left font-medium text-slate-400';
  const td = 'px-3 py-2 tabular-nums';
  return (
    <div className="space-y-8">
      <p className="font-mono text-sm text-slate-400">
        {d.template} → {d.project} · as of {d.asOfDate} · {d.periodsPerYear} periods/year ({d.frequencySource})
      </p>
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full text-sm">
          <thead className="bg-white/5">
            <tr>
              {['Book', 'MTD', 'QTD', 'YTD', '1Y', '3Y', 'ITD', 'Sharpe', 'Max DD'].map((h) => (
                <th key={h} className={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.horizons.map((r) => (
              <tr key={r.book} className="border-t border-white/5">
                <td className="px-3 py-2">{r.book}</td>
                {[r.mtd, r.qtd, r.ytd, r.oneYr, r.threeYr, r.itd, r.sharpe, r.maxDd].map((v, i) => (
                  <td key={i} className={td}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <h3 className="text-lg font-medium">Reconciliation against the administrator</h3>
        <p className="mt-1 text-sm text-slate-400">
          {d.reconciliation.referenceFile} · {d.reconciliation.comparisons} comparisons · tolerance{' '}
          {d.reconciliation.toleranceBps} bp
        </p>
        <div className="mt-4 overflow-x-auto rounded-xl border border-white/10">
          <table className="w-full text-sm">
            <thead className="bg-white/5">
              <tr>
                {['Book', 'Month', 'Ours', 'Reference', 'Diff', 'Note'].map((h) => (
                  <th key={h} className={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.reconciliation.breaks.map((b) => (
                <tr key={b.book} className="border-t border-white/5">
                  <td className="px-3 py-2">{b.book}</td>
                  <td className={td}>{b.month}</td>
                  <td className={td}>{b.ours}</td>
                  <td className={td}>{b.reference}</td>
                  <td className={`${td} ${b.diffBps.startsWith('0.0') ? '' : 'text-amber-300'}`}>{b.diffBps}</td>
                  <td className="px-3 py-2 text-slate-400">{b.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Tools() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {FALLBACK_TOOLS.map((t) => (
        <div key={t.name} className="rounded-xl border border-white/10 p-5">
          <div className="flex items-center justify-between">
            <span className="font-mono text-sm text-emerald-300">{t.name}</span>
            <span className="text-xs text-slate-500">
              {t.toolset} · v{t.version}
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-400">{t.description}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {t.permissions.map((p) => (
              <Pill key={p}>{p}</Pill>
            ))}
            {t.hasMcpAppView && <Pill>MCP App view</Pill>}
          </div>
        </div>
      ))}
    </div>
  );
}

function Tutorial() {
  const [open, setOpen] = useState(TUTORIAL_LABS[0].id);
  const lab = TUTORIAL_LABS.find((l) => l.id === open) ?? TUTORIAL_LABS[0];
  return (
    <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
      <ol className="space-y-1">
        {TUTORIAL_LABS.map((l) => (
          <li key={l.id}>
            <button
              onClick={() => setOpen(l.id)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                l.id === open ? 'bg-emerald-500/10 text-emerald-200' : 'text-slate-400 hover:bg-white/5'
              }`}
            >
              <span>
                {l.id}. {l.title}
              </span>
              <span className="text-xs text-slate-500">{l.time}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="rounded-xl border border-white/10 p-6">
        <p className="text-xs text-slate-500">For {lab.audience}</p>
        <h3 className="mt-1 text-xl font-medium">{lab.title}</h3>
        <p className="mt-2 text-sm text-slate-400">{lab.goal}</p>
        <div className="mt-4 flex items-center gap-2 text-xs text-slate-500">
          <Terminal size={14} /> Command
        </div>
        <pre className="mt-1 overflow-x-auto rounded-lg bg-black/40 p-4 text-xs text-slate-200">{lab.command}</pre>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-black/40 p-4 text-xs text-slate-400">{lab.sampleOutput}</pre>
        <p className="mt-4 flex gap-2 text-sm text-slate-300">
          <Check size={16} className="mt-0.5 shrink-0 text-emerald-400" /> {lab.whatThisShows}
        </p>
      </div>
    </div>
  );
}

function Proof() {
  return (
    <div className="space-y-12">
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {PERSONAS.map((p) => (
          <div key={p.id} className="rounded-xl border border-white/10 p-5">
            <h3 className="font-medium">{p.role}</h3>
            <p className="mt-2 text-sm text-slate-500">{p.concern}</p>
            <p className="mt-2 text-sm text-slate-300">{p.value}</p>
            <p className="mt-2 text-xs text-emerald-300">{p.proof}</p>
          </div>
        ))}
      </div>
      <div>
        <h3 className="mb-4 text-lg font-medium">Tested on five mock funds</h3>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {MOCK_FUNDS.map((f) => (
            <div key={f.name} className="rounded-xl border border-white/10 p-5">
              <h4 className="font-medium">{f.name}</h4>
              <p className="text-xs text-slate-500">{f.profile}</p>
              <p className="mt-2 font-mono text-xs text-slate-400">{f.stack}</p>
              <p className="mt-2 text-sm text-slate-300">{f.outcome}</p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h3 className="mb-4 text-lg font-medium">Against the alternatives</h3>
        <div className="space-y-3">
          {COMPETITIVE_MATRIX.map((c) => (
            <div key={c.alternative} className="grid gap-3 rounded-xl border border-white/10 p-5 md:grid-cols-4">
              <div className="font-medium">{c.alternative}</div>
              <div className="text-sm text-slate-400">{c.strength}</div>
              <div className="text-sm text-slate-400">{c.gap}</div>
              <div className="text-sm text-emerald-300">{c.qaosEdge}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <div className="bg-grid-pattern min-h-screen">
      <header className="sticky top-0 z-10 border-b border-white/10 bg-[#080B10]/85 backdrop-blur">
        <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <a href="#top" className="flex items-center gap-2 font-semibold">
            <img src="/logo-mark.svg" alt="" className="h-8 w-auto" />
            <span>
              Quant-Agent-<span className="text-emerald-400">OS</span>
            </span>
          </a>
          <ul className="hidden gap-6 text-sm text-slate-400 md:flex">
            {NAV.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`} className="hover:text-white">{label}</a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="top">
        <div className="mx-auto max-w-6xl px-6 pt-24 pb-8">
          <Pill>Self-hosted · source-available · read-only by default</Pill>
          <h1 className="mt-5 max-w-3xl text-5xl font-semibold leading-tight tracking-tight">
            Your AI clients, connected to your own Python tools, data, and cited research.
          </h1>
          <p className="mt-5 max-w-2xl text-lg text-slate-400">
            Start from tested quantitative applications, own the code, and serve it over MCP, REST, and a Python SDK on
            infrastructure you run. Every number is computed, every claim is cited, every call is audited.
          </p>
          <div className="mt-8 flex gap-3">
            <a href="#demo" className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-black hover:bg-emerald-400">
              <Play size={16} /> Try the live demo
            </a>
            <a href="#tutorial" className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-5 py-2.5 text-sm hover:border-white/30">
              <Wrench size={16} /> Follow the tutorial
            </a>
          </div>
        </div>

        <Section
          id="demo"
          title="Live demo"
          lead="Each scenario calls a real tool on the local demo server through the bridge. Without the bridge running, the page shows the recorded response and says so."
        >
          <Demo />
        </Section>
        <Section
          id="one-hour"
          title="The one-hour test"
          lead="Returns in, a horizon table and an administrator reconciliation out. The sample figures are fictional."
        >
          <OneHourTest />
        </Section>
        <Section id="tools" title="Tools" lead="What an AI client can call, each with its declared permissions.">
          <Tools />
        </Section>
        <Section id="tutorial" title="Tutorial" lead="Ten labs from a first report to wrapping legacy Python.">
          <Tutorial />
        </Section>
        <Section id="proof" title="Proof" lead="Who it is for, where it was tried, and what it is not.">
          <Proof />
        </Section>
      </main>

      <footer className="border-t border-white/10 py-8 text-center text-sm text-slate-500">
        Quant-Agent-OS · Business Source License 1.1 · Quant Toolbox and templates MIT
      </footer>
    </div>
  );
}
