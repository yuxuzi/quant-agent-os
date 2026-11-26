export type PersonaId = 'growth-pm' | 'value-analyst' | 'auditor';

export interface DemoScenario {
  id: string;
  label: string;
  badge: string;
  persona: PersonaId;
  prompt: string;
  tool: string;
  arguments: Record<string, unknown>;
  expectedStatus: 'ok' | 'denied' | 'abstained';
  summary: string;
  fallbackResponse: {
    duration_ms: number;
    is_error: boolean;
    result: Record<string, unknown>;
  };
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: 'growth-var',
    label: 'Firm VaR by Convention',
    badge: 'Firm Tool · Computed',
    persona: 'growth-pm',
    prompt: 'What is the growth book 95% one-month VaR by our firm convention?',
    tool: 'book_var',
    arguments: { book: 'growth', confidence: 0.95 },
    expectedStatus: 'ok',
    summary:
      'Runs the firm’s own book_var Python tool by dataset reference. Raw asset returns never enter the LLM.',
    fallbackResponse: {
      duration_ms: 14.2,
      is_error: false,
      result: {
        book: 'growth',
        var: 0.0772,
        weights: { ALDER: 0.35, BIRCH: 0.3, CEDAR: 0.2, DOGWOOD: 0.15 },
        convention: 'Historical, nearest rank, one period, at current weights, no scaling.',
        periods: 36,
        data_versions: {
          'dataset:growth-weights': 'sha256:8f4e2b9a1c30d411',
          'dataset:growth-assets': 'sha256:d3678f776ae52953',
        },
        evidence: [
          {
            kind: 'computed',
            statement: 'The 95% historical VaR of the growth book is 7.72% of capital over one period.',
            sources: [{ source_id: 'growth-assets', location: '36 periods' }],
          },
        ],
      },
    },
  },
  {
    id: 'growth-risk',
    label: 'Factor Risk Decomposition',
    badge: 'Quant Toolbox · Reference',
    persona: 'growth-pm',
    prompt: 'Decompose the growth book’s volatility and factor exposures against our factors dataset.',
    tool: 'analyze_risk',
    arguments: {
      portfolio_id: 'growth',
      weights: { ALDER: 0.35, BIRCH: 0.3, CEDAR: 0.2, DOGWOOD: 0.15 },
      asset_returns: { dataset: 'growth-assets' },
      factor_returns: { dataset: 'factors', columns: ['market', 'size', 'value', 'momentum'] },
    },
    expectedStatus: 'ok',
    summary:
      'Euler volatility contributions + OLS factor exposures computed deterministically in Quant Toolbox from dataset IDs.',
    fallbackResponse: {
      duration_ms: 21.8,
      is_error: false,
      result: {
        portfolio_id: 'growth',
        volatility: 0.1703,
        parametric_var: -0.0862,
        concentration: { herfindahl: 0.275, effective_positions: 3.64 },
        factors: {
          exposures: { market: 1.15, size: 0.22, value: -0.34, momentum: 0.51 },
          r_squared: 0.986,
        },
        conventions: { source: 'numpy sample covariance and least squares', periods_per_year: 12 },
        data_versions: {
          'dataset:growth-assets': 'sha256:d3678f776ae52953',
          'dataset:factors': 'sha256:5b37ee735c5b8b64',
        },
        evidence: [
          {
            kind: 'computed',
            statement: 'Annualised volatility 17.03%; momentum factor exposure +0.51.',
            sources: [{ source_id: 'growth-assets', location: '36 periods' }],
          },
        ],
      },
    },
  },
  {
    id: 'research-citation',
    label: 'Cited Research Retrieval',
    badge: 'Quant Research · Cited',
    persona: 'growth-pm',
    prompt: 'Does our internal research say anything about momentum crash risk in concentrated growth books?',
    tool: 'find_related_signals',
    arguments: { query: 'momentum crash risk in growth portfolios' },
    expectedStatus: 'ok',
    summary:
      'Retrieves exact passages with file and line citations plus extracted fact paths. Retrieved text stays marked untrusted.',
    fallbackResponse: {
      duration_ms: 9.4,
      is_error: false,
      result: {
        abstained: false,
        snapshot_id: 'snap:9c2e41a8',
        passages: [
          {
            chunk_id: 'momentum-crash-risk.md#1',
            source: { source_id: 'momentum-crash-risk.md', location: 'lines 13-18' },
            text: 'Momentum contradicts the reversal effect at the one-month horizon. Momentum crashes follow market rebounds. Concentrated growth portfolios often carry a positive momentum exposure without having chosen it.',
          },
          {
            chunk_id: 'factor-crowding.md#2',
            source: { source_id: 'factor-crowding.md', location: 'lines 9-14' },
            text: 'Crowded momentum legs exhibit acute left-tail convexity during sharp factor rotations.',
          },
        ],
        warnings: [],
      },
    },
  },
  {
    id: 'research-report',
    label: 'Deterministic Composite Report',
    badge: 'MCP Composite · No LLM',
    persona: 'growth-pm',
    prompt: 'Give me a report on momentum with the growth book performance against its benchmark.',
    tool: 'research_report',
    arguments: {
      focus: 'momentum',
      returns: { dataset: 'growth-book', column: 'book' },
      benchmark: { dataset: 'growth-book', column: 'benchmark' },
    },
    expectedStatus: 'ok',
    summary:
      'One deterministic composite tool call keeping retrieved research passages and computed Sharpe/drawdown strictly separate.',
    fallbackResponse: {
      duration_ms: 28.5,
      is_error: false,
      result: {
        focus: 'momentum',
        metrics: {
          annualized_return: 0.0412,
          annualized_volatility: 0.1703,
          sharpe_ratio: -0.05,
          max_drawdown: -0.142,
          beta: 1.14,
          alpha: -0.018,
        },
        evidence: [
          {
            kind: 'retrieved',
            statement: 'Momentum contradicts the reversal effect at the one-month horizon.',
            sources: [{ source_id: 'momentum-crash-risk.md', location: 'lines 13-15' }],
          },
          {
            kind: 'computed',
            statement: 'Sharpe ratio -0.05; maximum drawdown -14.20% over 36 months.',
            sources: [{ source_id: 'growth-book', location: 'column book' }],
          },
        ],
      },
    },
  },
  {
    id: 'access-refusal',
    label: 'Cross-Desk Access Refusal',
    badge: 'Fail-Closed Guard',
    persona: 'growth-pm',
    prompt: 'Show me the value desk book performance (growth-pm trying to read value-book).',
    tool: 'analyze_performance',
    arguments: { returns: { dataset: 'value-book', column: 'book' } },
    expectedStatus: 'denied',
    summary:
      'Growth-desk cannot see value-book. To the AI client it looks like a missing dataset; the Compliance Auditor sees the exact refusal.',
    fallbackResponse: {
      duration_ms: 4.1,
      is_error: true,
      result: {
        status: 'denied',
        error: "Unknown dataset 'value-book'.",
        audit_note: 'Recorded in runs.db as refused: dataset.read value-book for growth-pm.',
      },
    },
  },
  {
    id: 'abstention',
    label: 'Zero-Hit Abstention',
    badge: 'Abstention · No Hallucination',
    persona: 'growth-pm',
    prompt: 'What is the duration of our municipal bond holdings?',
    tool: 'find_related_signals',
    arguments: { query: 'What is the duration of our municipal bond holdings?' },
    expectedStatus: 'abstained',
    summary:
      'When internal documents contain no matching evidence, Quant Research explicitly abstains and returns zero passages.',
    fallbackResponse: {
      duration_ms: 6.7,
      is_error: false,
      result: {
        abstained: true,
        passages: [],
        warnings: [
          'No passages matched the query in the corpus; abstaining rather than guessing.',
        ],
      },
    },
  },
];

export const ONE_HOUR_TEST_DATA = {
  project: 'kestrel-perf-report',
  template: 'quant-apps/performance-report',
  periodsPerYear: 12,
  frequencySource: 'auto-inferred from monthly dates (median spacing 30.4 days)',
  asOfDate: '2026-10-31',
  horizons: [
    { book: 'Global Multi-Strategy (Net)', mtd: '+1.42%', qtd: '+1.42%', ytd: '+11.84%', oneYr: '+14.21%', threeYr: '+10.65%', itd: '+12.08%', sharpe: '1.38', maxDd: '-4.92%' },
    { book: 'Growth Equities Sleeve', mtd: '+2.18%', qtd: '+2.18%', ytd: '+15.40%', oneYr: '+18.92%', threeYr: '+13.10%', itd: '+14.45%', sharpe: '1.19', maxDd: '-8.40%' },
    { book: 'Credit & Carry Sleeve', mtd: '+0.64%', qtd: '+0.64%', ytd: '+7.92%', oneYr: '+9.45%', threeYr: '+8.12%', itd: '+8.87%', sharpe: '1.74', maxDd: '-2.15%' },
  ],
  reconciliation: {
    referenceFile: 'admin_nav_statement_2026_10.xlsx (sheet="Monthly Net", header_row=6, unit="percent")',
    sha256: 'e3b0c44298fc1c149afbf4c8996fb924',
    toleranceBps: 1.0,
    comparisons: 36,
    breaks: [
      { book: 'Global Multi-Strategy (Net)', month: '2026-08', ours: '+0.842%', reference: '+0.825%', diffBps: '+1.7 bps', note: 'Accrued audit fee adjustment in admin restatement' },
      { book: 'Growth Equities Sleeve', month: '2026-09', ours: '-1.210%', reference: '-1.210%', diffBps: '0.0 bps', note: 'Reconciled within tolerance' },
      { book: 'Credit & Carry Sleeve', month: '2026-10', ours: '+0.640%', reference: '+0.638%', diffBps: '+0.2 bps', note: 'Reconciled within tolerance' },
    ],
  },
};

export interface ToolCatalogEntry {
  name: string;
  toolset: 'research' | 'analytics' | 'factory' | 'firm';
  version: string;
  permissions: string[];
  description: string;
  sampleArgs: Record<string, unknown>;
  hasMcpAppView?: string;
}

export const FALLBACK_TOOLS: ToolCatalogEntry[] = [
  {
    name: 'book_var',
    toolset: 'firm',
    version: '1.0.0',
    permissions: ['read_local_data'],
    description: 'Firm-owned Python function registered via FirmApp.expose: historical nearest-rank VaR at current weights.',
    sampleArgs: { book: 'growth', confidence: 0.95 },
  },
  {
    name: 'analyze_performance',
    toolset: 'analytics',
    version: '2.1.0',
    permissions: ['read_local_data'],
    description: 'Full return, ratio, drawdown, and statistical confidence suite from Quant Toolbox. Serves MCP App UI.',
    sampleArgs: { returns: { dataset: 'growth-book', column: 'book' }, benchmark: { dataset: 'growth-book', column: 'benchmark' } },
    hasMcpAppView: 'ui://quant-agent-os/performance.html',
  },
  {
    name: 'analyze_risk',
    toolset: 'analytics',
    version: '2.3.0',
    permissions: ['read_local_data'],
    description: 'Euler volatility contributions, Cornish-Fisher VaR, factor exposures, historical stress, and factor models.',
    sampleArgs: {
      portfolio_id: 'growth',
      weights: { ALDER: 0.35, BIRCH: 0.3, CEDAR: 0.2, DOGWOOD: 0.15 },
      asset_returns: { dataset: 'growth-assets' },
      factor_returns: { dataset: 'factors', columns: ['market', 'size', 'value', 'momentum'] },
    },
  },
  {
    name: 'find_related_signals',
    toolset: 'research',
    version: '1.2.0',
    permissions: ['read_local_data'],
    description: 'Cited BM25 + entity-link + fact-graph (+ optional vector) retrieval over the firm corpus with source filters.',
    sampleArgs: { query: 'momentum crash risk in growth portfolios' },
  },
  {
    name: 'get_source_passage',
    toolset: 'research',
    version: '1.1.0',
    permissions: ['read_local_data'],
    description: 'Fetches a cited chunk by id with optional surrounding context passages in reading order.',
    sampleArgs: { chunk_id: 'momentum-crash-risk.md#1', context: 1 },
  },
  {
    name: 'research_report',
    toolset: 'research',
    version: '1.0.0',
    permissions: ['read_local_data'],
    description: 'Deterministic composite workflow combining cited signal spec + computed performance metrics.',
    sampleArgs: {
      focus: 'momentum',
      returns: { dataset: 'growth-book', column: 'book' },
      benchmark: { dataset: 'growth-book', column: 'benchmark' },
    },
  },
  {
    name: 'list_datasets',
    toolset: 'analytics',
    version: '1.0.0',
    permissions: ['read_local_data'],
    description: 'Lists datasets permitted to the authenticated caller along with row/column counts and SHA-256 fingerprints.',
    sampleArgs: {},
  },
  {
    name: 'describe_template',
    toolset: 'factory',
    version: '1.0.0',
    permissions: ['read_only'],
    description: 'Inspects a Quant App Factory template profile (alpha-research, strategy-backtest, risk-monitor, performance-report).',
    sampleArgs: { template: 'quant-apps', profile: 'performance-report' },
  },
];

export interface TutorialLab {
  id: number;
  title: string;
  time: string;
  audience: string;
  goal: string;
  command: string;
  sampleOutput: string;
  whatThisShows: string;
}

export const TUTORIAL_LABS: TutorialLab[] = [
  {
    id: 1,
    title: 'Your First Report',
    time: '5 min',
    audience: 'CEO, COO, Engineer',
    goal: 'Run one deterministic cycle with zero AI model involved and observe evidence separation + refusal handling.',
    command: 'uv run python examples/portfolio-research/portfolio_research.py',
    sampleOutput: `## Findings
- computed: Annualized return of synthetic-balanced-2025 is 4.82%. [sources: synthetic-balanced-2025 (12 periods)]
- computed: Maximum drawdown of synthetic-balanced-2025 is -2.10%. [sources: synthetic-balanced-2025 (12 periods)]
## Provenance
| 1 | annualized_return | 1.0.0 | ok | 0.188 ms |
| 2 | max_drawdown      | 1.0.0 | ok | 0.118 ms |`,
    whatThisShows: 'Every figure names its evidence kind (computed), tool version, and run ID. Unknown or refused tools never halt valid steps.',
  },
  {
    id: 2,
    title: 'Numbers by Reference',
    time: '5 min',
    audience: 'Head of Quant, PM',
    goal: 'Compute Sharpe, beta, alpha, and Euler risk shares by dataset ID so raw returns never enter the model.',
    command: `uv run qaos data add examples/tutorial/data/fund_returns.csv --id fund
uv run python examples/tutorial/lab_analytics.py`,
    sampleOutput: `Added dataset fund: 36 rows, columns fund, benchmark (sha256 cab59d43f0a99633).
Its dates show 12 periods per year.
  annualized_return    0.1054
  sharpe_ratio         1.1043
  max_drawdown        -0.0803
  Sharpe convention: mean excess over sample deviation, times sqrt(periods per year)`,
    whatThisShows: 'Every result states its convention and data fingerprint. Corrupt CSVs are refused at ingestion; thin samples draw explicit warnings.',
  },
  {
    id: 3,
    title: 'Generate a Risk Application',
    time: '10 min',
    audience: 'CTO, Quant Engineer',
    goal: 'Generate a standalone, tested risk-monitor project owned 100% by the firm from one AppSpec.',
    command: `uv run qaos app new risk-monitor desk-risk --defaults --yes
cd desk-risk && uv run desk-risk report && uv run desk-risk check`,
    sampleOutput: `Created desk-risk (quant-apps/risk-monitor) -> 23 files
  ok  uv sync (0.8 s)
  ok  ruff check . (0.4 s)
  ok  mypy desk_risk tests (2.9 s)
  ok  pytest (2.2 s)`,
    whatThisShows: 'You get a working, MIT-owned Python project in seconds—with known-answer tests that fail if someone later breaks a risk metric.',
  },
  {
    id: 4,
    title: 'Configure & Customize (4 Levels)',
    time: '15 min',
    audience: 'Analyst, Engineer',
    goal: 'Adjust risk limits, conventions, and firm-specific sections in config.toml and firm/ without touching core formulas.',
    command: 'uv run desk-risk check   # known-answer tests guard every customization',
    sampleOutput: `4 levels of customization:
  1. Settings (annualization, benchmark, calendar) in config.toml
  2. Tear sheet sections & audiences in config.toml
  3. Firm-specific calculations in firm/ (never overwritten)
  4. Full Python source code ownership guarded by pytest`,
    whatThisShows: 'An analyst edits config.toml or uses a coding assistant; tests immediately catch any drift in Sharpe or VaR math.',
  },
  {
    id: 5,
    title: 'Serve to AI Clients (MCP)',
    time: '15 min',
    audience: 'PM, CTO',
    goal: 'Expose the generated application + Quant Research over MCP 2026-07-28 to Claude Code, Cursor, or VS Code.',
    command: 'claude mcp add --transport http qaos-demo http://127.0.0.1:8000/mcp --header "Authorization: Bearer $PM"',
    sampleOutput: `MCP handshake: 2026-07-28 (2025-11-25 supported)
Registered 12 tools across analytics, research, and firm namespaces.
UI extension advertised: ui://quant-agent-os/performance.html`,
    whatThisShows: 'One MCP server serves all tools, resources, prompts, and interactive MCP App views through the same audited executor.',
  },
  {
    id: 6,
    title: 'Authentication: Who Gets In',
    time: '10 min',
    audience: 'Security Lead, CTO',
    goal: 'Enforce hashed API keys with scopes or Entra/Cognito JWKS OAuth tokens; revoke keys without restarting.',
    command: 'uv run qaos keys new --principal growth-pm --scope tools:list --scope tools:call --group growth-desk',
    sampleOutput: `Anonymous GET /v1/tools -> HTTP 401 Unauthorized
Read-only key POST /v1/approvals -> HTTP 403 Forbidden (missing runs:approve)`,
    whatThisShows: 'Zero unauthenticated network calls succeed. Keys are stored as hashes and support expiration and instant revocation.',
  },
  {
    id: 7,
    title: 'Authorization & Audit Trail',
    time: '15 min',
    audience: 'Security Lead, Compliance',
    goal: 'Isolate desks via access.toml so refused datasets look missing and every attempt is logged for compliance.',
    command: 'uv run qaos runs report --from 2026-11-01 --to 2026-12-01',
    sampleOutput: `Principal   Tool                 Calls  Refused  Approvals
growth-pm   book_var                 3        0          0
growth-pm   analyze_performance      2        1          0   (refused: dataset.read value-book)
value-anal  find_related_signals     4        0          0`,
    whatThisShows: 'Refused datasets do not leak existence to the model. Compliance gets structured usage counts with zero sensitive content.',
  },
  {
    id: 8,
    title: 'Permissions & Approvals',
    time: '10 min',
    audience: 'Security Lead, CEO',
    goal: 'Guarantee the server stays read-only by default and requires single-use human approval for consequential actions.',
    command: 'uv run qaos app create desk-risk.toml   # triggers single-use approval bound to input SHA-256',
    sampleOutput: `ToolExecutor status: approval_required
Approval bound to: run_id + tool_name + input_sha256 (single-use, non-reusable)`,
    whatThisShows: 'An AI client can never trigger side effects or external network calls on its own—nor reuse an approval for altered arguments.',
  },
  {
    id: 9,
    title: 'Cited Research Corpus',
    time: '15 min',
    audience: 'Quant Researcher',
    goal: 'Ingest internal PDFs/Markdown notes (with optional RAGFlow DeepDoc or local ONNX embeddings) and query with line citations.',
    command: 'uv run qaos ingest ~/research && uv run qaos search "short-term reversal"',
    sampleOutput: `Ingested 21 papers (snap:9c2e41a8).
Hit #1: momentum-crash-risk.md (lines 13-18) — score 0.94
Abstention check: "municipal bond duration" -> 0 hits (abstained=true)`,
    whatThisShows: 'Ingestion is operator-only (agents stay read-only). Every claim links to exact file/page/line coordinates.',
  },
  {
    id: 10,
    title: 'Wrap Existing Legacy Python',
    time: '15 min',
    audience: 'CTO, Quant Dev',
    goal: 'Scan existing firm Python packages with AST (no import needed) and draft FirmApp.expose wrappers + check_app tests.',
    command: 'uv run qaos legacy scan ./kestrel_quant --write inventory.toml && uv run qaos legacy wrap inventory.toml --out ./legacy_tools',
    sampleOutput: `Scanned 26 callables in kestrel_quant:
  - 14 expose as is
  -  4 needs facade (panel -> as_of row)
  -  8 needs isolated service (stateful engine)
Generated legacy_tools/server.py + test_app.py (check_app)`,
    whatThisShows: 'Your existing VaR, pricing, and factor libraries become governed MCP tools in minutes instead of rewrites.',
  },
];

export const MOCK_FUNDS = [
  {
    name: 'Kestrel Point Capital',
    profile: 'Multi-Asset Pilot Fund',
    stack: 'SQLite Warehouse · Legacy Python · RFC 7662 IdP',
    outcome: 'Passed full 8-step pilot drill, legacy wrapping (26 -> 8 services), and known-answer signal parity.',
  },
  {
    name: 'Sablecroft Credit',
    profile: 'CLO Manager · Azure',
    stack: 'Azure Container Apps · SQL Server · Entra JWKS',
    outcome: 'Validated `/readyz` SQL probes, driver image build (`Dockerfile.firm`), and service error isolation.',
  },
  {
    name: 'Larchmere Equity',
    profile: 'Long-Only Equity · AWS',
    stack: 'AWS ECS Fargate · Snowflake · Admin Excel Recon',
    outcome: 'Validated `performance-report` workbook reconciliation (`[reference]`) and per-book benchmarks.',
  },
  {
    name: 'Corvane Allocators',
    profile: 'Alternatives Allocator · Azure',
    stack: 'Azure · Databricks · Multi-Series Analytics',
    outcome: 'Validated `analyze_risk` carried-forward price warnings and MCP App `performance.html` layout.',
  },
  {
    name: 'Wrenfield Partners',
    profile: 'Credit Hedge Fund · AWS',
    stack: 'AWS · PostgreSQL + DuckDB · Cognito JWKS',
    outcome: 'Validated `SqlDatasetStore` missing-column diagnostics, JSON logs, and `NAME_FILE` container secrets.',
  },
];

export const COMPETITIVE_MATRIX = [
  {
    alternative: 'DIY MCP Server (FastMCP / SDK)',
    strength: 'First tool takes a couple days; you own the code.',
    gap: 'Per-user dataset guards, single-use approvals, audit trail, numerical acceptance tests, and retrieval evals remain unbuilt.',
    qaosEdge: 'Ships complete Core governance + deterministic financial testing for ~20% of one NY quant dev base salary.',
  },
  {
    alternative: 'Generic MCP Gateways (Kong / MintMCP)',
    strength: 'Access control and logging in front of any MCP endpoint.',
    gap: 'Zero financial formulas, zero cited research retrieval, zero administrator reconciliation, zero app templates.',
    qaosEdge: 'Complementary—you can place a gateway in front of QAOS, while QAOS delivers the verified financial tools.',
  },
  {
    alternative: 'Free Libraries (QuantStats / pyfolio / alphalens)',
    strength: 'Quick tear sheets or factor plots at zero license cost.',
    gap: 'Not a firm standard: inconsistent conventions, no admin reconciliation, no MCP governance, sparse maintenance.',
    qaosEdge: 'Quant Toolbox locks `empyrical` & `numpy` parity to `1e-12` and wraps every metric in governed tools & tested apps.',
  },
  {
    alternative: 'Coding Assistant Alone (Claude / Cursor)',
    strength: 'Fast blank-page Python prototypes.',
    gap: 'Plausible code without known-answer tests, bias checks, or multi-user MCP permissions & audit.',
    qaosEdge: 'Templates give your assistant working code, stated conventions, and `pytest` guardrails that catch math regressions.',
  },
];

export const PERSONAS = [
  {
    id: 'cto',
    role: 'CTO / Head of Tech',
    concern: 'Architecture, zero vendor lock-in, maintainability, and fast integration of existing Python.',
    value: 'Clean layered Python (`uv`), 3-line `FirmApp.expose`, `qaos legacy scan`, Docker/ECS/ACA ready with `/readyz` & JSON logs.',
    proof: '1.0.0 Semver Promise (Decision 0035) + Continuity Clause on perpetual internal source rights.',
  },
  {
    id: 'quant',
    role: 'Head of Quant Research',
    concern: 'Numerical correctness, preventing look-ahead bias, and reliable citation without hallucinations.',
    value: 'Deterministic `Quant Toolbox` (`1e-12` parity), lagged signal/backtest conventions, explicit abstention on zero hits.',
    proof: 'Measured on 21 arXiv papers (53 gold questions): 19/19 topic recall, 28/28 off-topic abstention.',
  },
  {
    id: 'coo',
    role: 'COO / Investor Relations',
    concern: 'Numbers matching the fund administrator before investor letters or board decks go out.',
    value: 'The One-Hour Test: `performance-report` turns returns CSV/Excel into horizon tables & bps break reconciliation.',
    proof: 'Dated `reconcile.json` audit folder tracks every restatement and break down to 0.1 bps.',
  },
  {
    id: 'pm',
    role: 'Portfolio Manager',
    concern: 'Getting immediate answers on book risk and research notes inside Claude or Cursor.',
    value: 'Ask natural questions, inspect interactive `ui://` MCP App charts/tables right in chat, never paste CSVs.',
    proof: 'Datasets stay by reference; `research_report` separates computed facts from retrieved commentary.',
  },
  {
    id: 'security',
    role: 'Security & Compliance Lead',
    concern: 'Sensitive positions/alpha leaving the firm, unauthorized access, and auditability.',
    value: '100% customer-hosted (zero vendor telemetry/access), read-only default, Entra/Cognito JWKS, `qaos runs report`.',
    proof: 'Third security review completed for 1.0.0; refused datasets look non-existent to callers.',
  },
];

export const COMMERCIAL_LADDER = [
  {
    step: '00 · Evaluate',
    name: 'Public Source & Local Demo',
    price: '$0',
    cadence: 'BSL 1.1 + MIT Templates',
    scope: 'Full source inspection, MIT Quant Toolbox & App Factory templates, and the local Portfolio Intelligence sandbox.',
    deliverable: 'Run the One-Hour Test on synthetic or local returns in your own environment.',
    highlight: false,
  },
  {
    step: '01 · Diagnose',
    name: 'Paid Assessment',
    price: '$5,000',
    cadence: 'Fixed Scope · 15h Cap',
    scope: 'Inventory existing Python tools, review security/VPC fit, define numerical & retrieval acceptance cases.',
    deliverable: 'Signed Pilot Statement of Work with pre-priced subscription option.',
    highlight: false,
  },
  {
    step: '02 · Prove',
    name: 'Portfolio Intelligence Pilot',
    price: '$25,000',
    cadence: '8–10 Weeks · 50/50 Milestone',
    scope: '1 customer-hosted deployment, 1 AI client (Claude/Cursor), 1 generated app (returns-to-report) + 2–3 firm tools, access rules & acceptance report.',
    deliverable: '90-day pilot license + objective `qaos eval` acceptance report + time-saved baseline.',
    highlight: true,
  },
  {
    step: '03 · Scale',
    name: 'Annual Production License',
    price: '$48,000',
    cadence: 'Per Firm / Year',
    scope: 'Full platform production license, all releases & template upgrade assistance, 6 support hrs/mo (next-business-day SLA).',
    deliverable: 'Includes Continuity Clause: perpetual internal right to run your last paid version.',
    highlight: false,
  },
];
