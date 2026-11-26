import { Fragment, useState } from 'react';
import type { ReactNode } from 'react';
import {
  ArrowRight,
  Check,
  ClipboardCheck,
  Database,
  FileSearch,
  Lock,
  MessagesSquare,
  Play,
  Server,
  ShieldCheck,
  Terminal,
  Wrench,
} from 'lucide-react';
import {
  COMPETITIVE_MATRIX,
  DEMO_SCENARIOS,
  FALLBACK_TOOLS,
  MOCK_FUNDS,
  ONE_HOUR_TEST_DATA,
  PERSONAS,
  TUTORIAL_LABS,
} from './data/qaosData';
import type { DemoScenario, PersonaId } from './data/qaosData';

interface CallResult {
  duration_ms: number;
  is_error: boolean;
  result: Record<string, unknown>;
  source: 'live' | 'recorded';
}

const NAV = [
  ['how', 'How it works'],
  ['demo', 'Live demo'],
  ['one-hour', 'One-hour test'],
  ['tools', 'Tools'],
  ['tutorial', 'Tutorial'],
  ['proof', 'Proof'],
] as const;

const PERSONA_LABEL: Record<PersonaId, string> = {
  'growth-pm': 'Growth desk PM',
  'value-analyst': 'Value desk analyst',
  auditor: 'Compliance auditor',
};

const STATUS_STYLE = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  denied: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  abstained: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
} as const;

const FACTS = [
  { value: '100%', label: 'Customer-hosted', note: 'Your servers, your data; no vendor access' },
  { value: 'Read-only', label: 'By default', note: 'Consequential calls need single-use approval' },
  { value: '1e-12', label: 'Metric parity', note: 'empyrical and numpy backends held equal' },
  { value: '28 / 28', label: 'Off-topic abstentions', note: 'Measured on 21 real arXiv papers' },
];

/** Render `code spans` written with backticks in the data file. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split('`').map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="rounded bg-white/[0.06] px-1 py-px text-[0.92em] text-emerald-200">
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

function Logo({ className = '' }: { className?: string }) {
  return <img src="/logo-mark.svg" alt="" className={className} />;
}

function Section({
  id,
  index,
  eyebrow,
  title,
  lead,
  shaded = false,
  children,
}: {
  id: string;
  index: string;
  eyebrow: string;
  title: string;
  lead: string;
  shaded?: boolean;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`scroll-mt-20 ${shaded ? 'border-y border-white/[0.06] bg-white/[0.02]' : ''}`}>
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-emerald-400">
          {index} · {eyebrow}
        </p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h2>
        <p className="mt-4 max-w-3xl text-base leading-relaxed text-slate-400">{lead}</p>
        <div className="mt-12">{children}</div>
      </div>
    </section>
  );
}

function Pill({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs ${
        className || 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
      }`}
    >
      {children}
    </span>
  );
}

function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border border-white/10 bg-[#0B1016]/80 p-6 ${className}`}>{children}</div>
  );
}

function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-white/[0.08] bg-[#080B10]/80 backdrop-blur-md">
      <nav className="mx-auto flex h-18 max-w-6xl items-center justify-between gap-6 px-6">
        <a href="#top" className="flex items-center gap-3">
          <Logo className="h-11 w-auto drop-shadow-[0_0_12px_rgba(16,185,129,0.35)]" />
          <span className="leading-tight">
            <span className="block text-lg font-semibold tracking-tight">
              Quant-Agent-<span className="text-emerald-400">OS</span>
            </span>
            <span className="block text-[10px] font-medium uppercase tracking-[0.22em] text-slate-500">
              Quantitative Intelligence Platform
            </span>
          </span>
        </a>
        <ul className="hidden items-center gap-7 text-sm text-slate-400 lg:flex">
          {NAV.map(([id, label]) => (
            <li key={id}>
              <a href={`#${id}`} className="transition hover:text-white">
                {label}
              </a>
            </li>
          ))}
        </ul>
        <a
          href="#demo"
          className="hidden items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black transition hover:bg-emerald-400 sm:inline-flex"
        >
          <Play size={14} /> Run the demo
        </a>
      </nav>
    </header>
  );
}

function Hero() {
  return (
    <div id="top" className="relative overflow-hidden">
      <div className="pointer-events-none absolute -top-40 right-[-10%] h-[38rem] w-[38rem] rounded-full bg-emerald-500/10 blur-3xl" />
      <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-6 pt-20 pb-16 lg:grid-cols-[1.25fr_1fr] lg:pt-28">
        <div>
          <Pill>Self-hosted · source-available · read-only by default</Pill>
          <h1 className="mt-6 text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl lg:text-[3.4rem]">
            Your AI clients, connected to <span className="text-emerald-400">your own</span> Python tools, data,
            and cited research.
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-slate-400">
            Start from tested quantitative applications, own the code, and serve it over MCP, REST, and a Python
            SDK on infrastructure you run. Numbers are computed, claims are cited, and every call is audited.
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <a
              href="#demo"
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-5 py-3 text-sm font-semibold text-black transition hover:bg-emerald-400"
            >
              <Play size={16} /> Try the live demo
            </a>
            <a
              href="#tutorial"
              className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-5 py-3 text-sm transition hover:border-white/35 hover:bg-white/5"
            >
              <Wrench size={16} /> Follow the tutorial
            </a>
          </div>
        </div>
        <div className="relative mx-auto w-full max-w-md">
          <div className="absolute inset-8 rounded-full bg-emerald-400/15 blur-2xl" />
          <Logo className="relative w-full drop-shadow-[0_0_40px_rgba(16,185,129,0.35)]" />
        </div>
      </div>
      <div className="relative mx-auto max-w-6xl px-6 pb-20">
        <dl className="grid gap-px overflow-hidden rounded-2xl border border-white/10 bg-white/10 sm:grid-cols-2 lg:grid-cols-4">
          {FACTS.map((f) => (
            <div key={f.label} className="bg-[#0A0E14] p-6">
              <dt className="text-xs uppercase tracking-[0.16em] text-slate-500">{f.label}</dt>
              <dd className="mt-2 text-3xl font-semibold tracking-tight text-white">{f.value}</dd>
              <dd className="mt-1 text-sm text-slate-400">{f.note}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function HowItWorks() {
  const steps = [
    {
      icon: MessagesSquare,
      title: 'Your AI client',
      body: 'Claude, Cursor, or VS Code asks a question and picks the tools. The platform has no planner and calls no model.',
      items: ['MCP over stdio or HTTP', 'REST and Python SDK', 'Skills as guidance only'],
    },
    {
      icon: Server,
      title: 'Quant-Agent-OS server',
      body: 'Runs on your infrastructure. Every call passes the same executor, whatever the client.',
      items: ['Declared permissions, checked', 'Per-caller access, fails closed', 'Single-use approvals', 'Audit trail of every run'],
      highlight: true,
    },
    {
      icon: Database,
      title: 'Your tools and data',
      body: 'Numbers stay by reference: tools take dataset ids, so returns never pass through the model.',
      items: ['Quant Toolbox metrics', "Your firm's own Python", 'SQL datasets', 'Cited research corpus'],
    },
  ];
  return (
    <div className="grid items-stretch gap-4 lg:grid-cols-[1fr_auto_1fr_auto_1fr]">
      {steps.map((s, i) => (
        <Fragment key={s.title}>
          {i > 0 && (
            <div className="hidden items-center justify-center text-emerald-500/60 lg:flex">
              <ArrowRight size={22} />
            </div>
          )}
          <Card className={s.highlight ? 'border-emerald-500/40 bg-emerald-500/[0.06]' : ''}>
            <s.icon size={22} className="text-emerald-400" />
            <h3 className="mt-4 text-lg font-medium">{s.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{s.body}</p>
            <ul className="mt-4 space-y-1.5 text-sm text-slate-300">
              {s.items.map((it) => (
                <li key={it} className="flex gap-2">
                  <Check size={15} className="mt-0.5 shrink-0 text-emerald-400" /> {it}
                </li>
              ))}
            </ul>
          </Card>
        </Fragment>
      ))}
    </div>
  );
}

function Demo() {
  const [active, setActive] = useState<DemoScenario>(DEMO_SCENARIOS[0]);
  const [out, setOut] = useState<CallResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(scenario: DemoScenario) {
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
    <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
      <ul className="space-y-2">
        {DEMO_SCENARIOS.map((s, i) => (
          <li key={s.id}>
            <button
              onClick={() => {
                setActive(s);
                setOut(null);
              }}
              className={`group flex w-full gap-3 rounded-xl border px-4 py-3 text-left transition ${
                s.id === active.id
                  ? 'border-emerald-500/50 bg-emerald-500/[0.08]'
                  : 'border-white/10 hover:border-white/25 hover:bg-white/[0.03]'
              }`}
            >
              <span className="font-mono text-xs text-slate-500">{String(i + 1).padStart(2, '0')}</span>
              <span>
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="mt-0.5 block text-xs text-slate-500">{s.badge}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <Card className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Pill className="border-white/15 bg-white/5 text-slate-300">{PERSONA_LABEL[active.persona]}</Pill>
          <Pill className={STATUS_STYLE[active.expectedStatus]}>expected: {active.expectedStatus}</Pill>
        </div>
        <p className="mt-4 text-xl leading-snug">“{active.prompt}”</p>
        <p className="mt-3 text-sm leading-relaxed text-slate-400">{active.summary}</p>
        <div className="mt-5 overflow-hidden rounded-xl border border-white/10 bg-black/40">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-xs text-slate-500">
            <span className="font-mono">tools/call</span>
            <span className="font-mono text-emerald-300">{active.tool}</span>
          </div>
          <pre className="overflow-x-auto p-4 text-xs leading-relaxed text-slate-300">
            {JSON.stringify(active.arguments, null, 2)}
          </pre>
        </div>
        <button
          onClick={() => void run(active)}
          disabled={busy}
          className="mt-5 inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-black transition hover:bg-emerald-400 disabled:opacity-50"
        >
          <Play size={15} /> {busy ? 'Calling…' : 'Call tool'}
        </button>
        {out && (
          <div className="mt-6 overflow-hidden rounded-xl border border-white/10 bg-black/40">
            <div className="flex flex-wrap items-center gap-3 border-b border-white/10 px-4 py-2 text-xs text-slate-400">
              <Pill
                className={
                  out.source === 'live'
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-white/15 bg-white/5 text-slate-300'
                }
              >
                {out.source === 'live' ? '● live MCP call' : 'recorded response'}
              </Pill>
              <span className="tabular-nums">{out.duration_ms} ms</span>
              <span className={out.is_error ? 'text-amber-300' : 'text-emerald-300'}>
                {out.is_error ? 'refused' : 'ok'}
              </span>
            </div>
            <pre className="max-h-96 overflow-auto p-4 text-xs leading-relaxed text-slate-300">
              {JSON.stringify(out.result, null, 2)}
            </pre>
          </div>
        )}
      </Card>
    </div>
  );
}

function OneHourTest() {
  const d = ONE_HOUR_TEST_DATA;
  const th = 'px-4 py-3 text-right font-medium text-slate-400 first:text-left';
  const td = 'px-4 py-3 text-right tabular-nums';
  const tone = (v: string) => (v.startsWith('-') ? 'text-rose-300' : v.startsWith('+') ? 'text-emerald-300' : '');
  return (
    <div className="space-y-10">
      <div className="flex flex-wrap gap-2 text-xs">
        <Pill className="border-white/15 bg-white/5 font-mono text-slate-300">{d.template}</Pill>
        <Pill className="border-white/15 bg-white/5 text-slate-300">as of {d.asOfDate}</Pill>
        <Pill className="border-white/15 bg-white/5 text-slate-300">
          {d.periodsPerYear} periods a year, {d.frequencySource}
        </Pill>
      </div>
      <div className="overflow-x-auto rounded-2xl border border-white/10 bg-[#0B1016]/80">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="border-b border-white/10 bg-white/[0.03]">
            <tr>
              {['Book', 'MTD', 'QTD', 'YTD', '1Y', '3Y', 'ITD', 'Sharpe', 'Max DD'].map((h) => (
                <th key={h} className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.horizons.map((r) => (
              <tr key={r.book} className="border-t border-white/5">
                <td className="px-4 py-3 font-medium">{r.book}</td>
                {[r.mtd, r.qtd, r.ytd, r.oneYr, r.threeYr, r.itd].map((v, i) => (
                  <td key={i} className={`${td} ${tone(v)}`}>
                    {v}
                  </td>
                ))}
                <td className={td}>{r.sharpe}</td>
                <td className={`${td} text-rose-300`}>{r.maxDd}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="flex items-center gap-2">
          <ClipboardCheck size={18} className="text-emerald-400" />
          <h3 className="text-lg font-medium">Reconciled against the administrator</h3>
        </div>
        <p className="mt-2 text-sm text-slate-400">
          {d.reconciliation.comparisons} comparisons at a tolerance of {d.reconciliation.toleranceBps} bp against{' '}
          <code className="text-slate-300">{d.reconciliation.referenceFile}</code>
        </p>
        <div className="mt-4 overflow-x-auto rounded-2xl border border-white/10 bg-[#0B1016]/80">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b border-white/10 bg-white/[0.03]">
              <tr>
                {['Book', 'Month', 'Ours', 'Administrator', 'Difference', 'Note'].map((h) => (
                  <th key={h} className="px-4 py-3 text-left font-medium text-slate-400">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.reconciliation.breaks.map((b) => {
                const isBreak = Math.abs(parseFloat(b.diffBps)) > d.reconciliation.toleranceBps;
                return (
                  <tr key={b.book} className="border-t border-white/5">
                    <td className="px-4 py-3 font-medium">{b.book}</td>
                    <td className="px-4 py-3 tabular-nums">{b.month}</td>
                    <td className="px-4 py-3 tabular-nums">{b.ours}</td>
                    <td className="px-4 py-3 tabular-nums">{b.reference}</td>
                    <td className="px-4 py-3">
                      <Pill
                        className={
                          isBreak
                            ? 'border-amber-500/40 bg-amber-500/10 tabular-nums text-amber-300'
                            : 'border-emerald-500/30 bg-emerald-500/10 tabular-nums text-emerald-300'
                        }
                      >
                        {isBreak ? 'break ' : ''}
                        {b.diffBps}
                      </Pill>
                    </td>
                    <td className="px-4 py-3 text-slate-400">{b.note}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const TOOLSET_ICON = { research: FileSearch, analytics: Database, factory: Wrench, firm: Server } as const;

function Tools() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {FALLBACK_TOOLS.map((t) => {
        const Icon = TOOLSET_ICON[t.toolset];
        return (
          <Card key={t.name} className="transition hover:border-emerald-500/30">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
              <div className="flex min-w-0 items-center gap-3">
                <span className="rounded-lg border border-white/10 bg-white/5 p-2">
                  <Icon size={16} className="text-emerald-400" />
                </span>
                <span className="truncate font-mono text-sm text-white">{t.name}</span>
              </div>
              <span className="shrink-0 font-mono text-xs text-slate-500">
                {t.toolset} · v{t.version}
              </span>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">{t.description}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {t.permissions.map((p) => (
                <Pill key={p} className="border-white/15 bg-white/5 font-mono text-slate-300">
                  <Lock size={11} className="mr-1.5" />
                  {p}
                </Pill>
              ))}
              {t.hasMcpAppView && <Pill>MCP App view</Pill>}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function Tutorial() {
  const [open, setOpen] = useState(TUTORIAL_LABS[0].id);
  const lab = TUTORIAL_LABS.find((l) => l.id === open) ?? TUTORIAL_LABS[0];
  return (
    <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
      <ol className="space-y-1">
        {TUTORIAL_LABS.map((l) => (
          <li key={l.id}>
            <button
              onClick={() => setOpen(l.id)}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition ${
                l.id === open ? 'bg-emerald-500/10 text-emerald-200' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
              }`}
            >
              <span className="w-5 font-mono text-xs text-slate-500">{l.id}</span>
              <span className="flex-1">{l.title}</span>
              <span className="text-xs text-slate-500">{l.time}</span>
            </button>
          </li>
        ))}
      </ol>
      <Card className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Pill>Lab {lab.id}</Pill>
          <Pill className="border-white/15 bg-white/5 text-slate-300">{lab.time}</Pill>
          <span className="text-xs text-slate-500">For {lab.audience}</span>
        </div>
        <h3 className="mt-4 text-2xl font-semibold tracking-tight">{lab.title}</h3>
        <p className="mt-2 leading-relaxed text-slate-400">{lab.goal}</p>
        <div className="mt-6 overflow-hidden rounded-xl border border-white/10 bg-black/40">
          <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2 text-xs text-slate-500">
            <Terminal size={13} /> Run
          </div>
          <pre className="whitespace-pre-wrap break-all p-4 text-xs leading-relaxed text-emerald-200">
            <span className="select-none text-slate-600">$ </span>
            {lab.command}
          </pre>
          <pre className="whitespace-pre-wrap break-words border-t border-white/10 p-4 text-xs leading-relaxed text-slate-400">
            {lab.sampleOutput}
          </pre>
        </div>
        <p className="mt-5 flex gap-2 text-sm leading-relaxed text-slate-300">
          <Check size={16} className="mt-0.5 shrink-0 text-emerald-400" /> {lab.whatThisShows}
        </p>
      </Card>
    </div>
  );
}

function Proof() {
  return (
    <div className="space-y-20">
      <div>
        <h3 className="text-xl font-medium">Who it is for</h3>
        <div className="mt-6 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {PERSONAS.map((p) => (
            <Card key={p.id} className="flex flex-col">
              <h4 className="font-medium">{p.role}</h4>
              <p className="mt-3 text-xs uppercase tracking-wider text-slate-500">Asks about</p>
              <p className="mt-1 text-sm leading-relaxed text-slate-400">{p.concern}</p>
              <p className="mt-3 text-xs uppercase tracking-wider text-slate-500">Gets</p>
              <p className="mt-1 text-sm leading-relaxed text-slate-300">
                <Rich text={p.value} />
              </p>
              <p className="mt-auto flex gap-2 border-t border-white/10 pt-3 text-xs leading-relaxed text-emerald-300">
                <ShieldCheck size={14} className="mt-px shrink-0" />
                <span>
                  <Rich text={p.proof} />
                </span>
              </p>
            </Card>
          ))}
        </div>
      </div>
      <div>
        <h3 className="text-xl font-medium">Tried at five mock funds</h3>
        <p className="mt-2 text-sm text-slate-400">Fictional firms, each with a different cloud, database, and sign-in.</p>
        <div className="mt-6 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {MOCK_FUNDS.map((f) => (
            <Card key={f.name}>
              <h4 className="font-medium">{f.name}</h4>
              <p className="mt-0.5 text-xs text-slate-500">{f.profile}</p>
              <p className="mt-3 font-mono text-xs text-emerald-300/80">{f.stack}</p>
              <p className="mt-3 text-sm leading-relaxed text-slate-300">
                <Rich text={f.outcome} />
              </p>
            </Card>
          ))}
        </div>
      </div>
      <div>
        <h3 className="text-xl font-medium">Against the alternatives</h3>
        <div className="mt-6 overflow-hidden rounded-2xl border border-white/10">
          <div className="hidden grid-cols-4 gap-6 border-b border-white/10 bg-white/[0.03] px-6 py-3 text-xs uppercase tracking-wider text-slate-500 md:grid">
            <span>Alternative</span>
            <span>Good at</span>
            <span>Leaves to you</span>
            <span className="text-emerald-400">Quant-Agent-OS</span>
          </div>
          {COMPETITIVE_MATRIX.map((c) => (
            <div
              key={c.alternative}
              className="grid gap-3 border-t border-white/5 bg-[#0B1016]/80 px-6 py-5 first:border-t-0 md:grid-cols-4 md:gap-6"
            >
              <div className="font-medium">{c.alternative}</div>
              <div className="text-sm leading-relaxed text-slate-400">{c.strength}</div>
              <div className="text-sm leading-relaxed text-slate-400">{c.gap}</div>
              <div className="text-sm leading-relaxed text-emerald-200">
                <Rich text={c.qaosEdge} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <footer className="border-t border-white/10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 px-6 py-10 sm:flex-row">
        <div className="flex items-center gap-3">
          <Logo className="h-8 w-auto" />
          <span className="text-sm font-semibold">
            Quant-Agent-<span className="text-emerald-400">OS</span>
          </span>
        </div>
        <p className="text-center text-xs text-slate-500">
          Business Source License 1.1 · Quant Toolbox and templates MIT · Sample figures are fictional
        </p>
      </div>
    </footer>
  );
}

export default function App() {
  return (
    <div className="bg-grid-pattern min-h-screen">
      <Header />
      <main>
        <Hero />
        <Section
          id="how"
          index="01"
          eyebrow="Architecture"
          title="How it works"
          lead="Your AI client orchestrates; the server only runs tools you registered, under rules you set. Business logic stays in Python, not in the protocol."
          shaded
        >
          <HowItWorks />
        </Section>
        <Section
          id="demo"
          index="02"
          eyebrow="Try it"
          title="Live demo"
          lead="Each scenario calls a real tool on the local demo server through the bridge. If the bridge is not running, the page shows a recorded response and labels it."
        >
          <Demo />
        </Section>
        <Section
          id="one-hour"
          index="03"
          eyebrow="Operations"
          title="The one-hour test"
          lead="Monthly returns in; a horizon table and a reconciliation against the fund administrator out, with every break explained."
          shaded
        >
          <OneHourTest />
        </Section>
        <Section
          id="tools"
          index="04"
          eyebrow="Catalog"
          title="Tools an AI client can call"
          lead="Each tool has a version and declared permissions that the executor checks on every call."
        >
          <Tools />
        </Section>
        <Section
          id="tutorial"
          index="05"
          eyebrow="Learn"
          title="Tutorial"
          lead="Ten labs, from a first report with no AI involved to serving your existing Python as governed tools."
          shaded
        >
          <Tutorial />
        </Section>
        <Section
          id="proof"
          index="06"
          eyebrow="Evidence"
          title="Proof"
          lead="Who it is for, where it was tried, and how it compares with what a firm would otherwise use."
        >
          <Proof />
        </Section>
      </main>
      <Footer />
    </div>
  );
}
