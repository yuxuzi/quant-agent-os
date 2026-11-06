# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **A notebook path.** `quant_platform.open_platform()` opens the home's knowledge store, datasets,
  and run log in one call. `platform.data.put("fund", df)` adds a pandas DataFrame or Series for the
  session, with the same date, alignment, and missing-value handling as a CSV file, and writes
  nothing to the home. See [getting-started.md](docs/getting-started.md#use-it-from-a-notebook).
- **Parquet datasets.** With the new `parquet` extra (pandas and pyarrow), `qaos data add` takes a
  `.parquet` or `.pq` file. It is converted to CSV when added, so tools and the server are unchanged,
  and the fingerprint is the stored CSV's. The dataset limits still apply.
- **A firm vocabulary.** `qaos ingest DIR --vocabulary firm.toml` merges your own signal and factor
  names over the bundled vocabulary. The store keeps the result, so serving and later ingestion match
  what ingestion saw.
- **API key expiry and reload.** `qaos keys new --expires-in-days N` sets an `expires` time on a
  key. A running HTTP server re-reads the keys file when it changes, so issuing or revoking a key
  needs no restart. **While the file is missing or invalid, the server accepts no key.**
- **A `busy` call status.** Each executor runs at most 16 tool calls at once in one bounded pool,
  instead of one new thread per call. A call beyond that returns `busy` with `retry_after_seconds`
  (REST: 503 with `Retry-After`), and spends no quota or approval. A timed-out call keeps its slot
  until its thread returns.
- **A local embedder.** The new `local-embeddings` extra adds `--embedder local`: semantic search
  with `BAAI/bge-small-en-v1.5` on your machine, off by default. On the 21 real papers it raises
  topic MRR from 0.899 to 1.0 and paraphrase hits from 0.833 to 1.0, and still abstains on all 28
  off-topic questions. Ingestion takes about 3 minutes instead of 10 seconds. It is not in the `all`
  extra because `onnxruntime` has no Python 3.15 wheel.
- **`record.result()`** returns a call's output, or raises `ToolCallError` with its status and a
  hint. `Platform.call` still never raises.

### Removed

- **The FTS5 fallback.** A SQLite without FTS5 is now refused with what to do, instead of ranking
  keyword search by a second, simpler path. Python builds from uv include FTS5.
- **The mock `hashing` embedder.** `--embedder hashing` and `open_research(vector=True)` are gone; it
  was a test double and now lives with the tests. Use `--embedder local`, or pass an `embedder`.

### Fixed

- **Noise reported as a Sharpe ratio.** `analyze_performance` returned figures such as 4.5e16 for a
  constant series, and a similar information ratio when returns and benchmark differ by a constant.
  Both are now undefined, as documented. Generated quant projects had a similar flaw: `beta` against a
  constant market returned noise and is now NaN. Existing generated projects keep the old code.
- **Two messages named the removed `dataset push`.** `list_datasets` and the undated-alignment
  refusal now name `qaos data add`.

- **The install command pins Python 3.13.** Without it, uv may choose 3.15, where the `analytics`
  extra compiles `bottleneck` from source and fails if the machine has no working C compiler.
- **The tutorial matches 0.2.0.** All its labs were run again: its setup check named a removed
  command, Lab 7's `show.py` no longer printed the open questions, and Lab 6 counted tools no longer
  served. It now uses one home, so `--store` and the other path flags are gone from its single-store
  labs.

## [0.2.0] - 2026-11-05

One install, one command, one home, and a smaller surface. **This release breaks existing setups,
with no migration:** the old commands and environment variables are gone, and files are not moved
for you. Install again from the tag, then run `qaos doctor`, which warns about a store in
`./.quant-research` or an old `factory-runs.db`; move those to the home by hand. Reconnect clients
with `qaos connect`, because their server command changed. See
[getting-started.md](docs/getting-started.md).

### Removed

- **The orchestrator** ([decision 0016](docs/decisions/0016-clients-orchestrate.md)):
  `Orchestrator`, `Platform.run`, `ModelProvider`, `FakeModelProvider`, `Plan`, the `plan-schema`
  command, and the `QUANT_AGENT_OS_MODEL_PROVIDER`, `_MODEL_NAME`, `_API_KEY`, and
  `_MAX_PLAN_STEPS` settings, which had no effect. Only a fake model could ever drive it; clients
  do the planning. `PlanStep` is now `ToolCall`, in `quant_agent_os.runtime`.
- **`generate_strategy` and `plan_app` are no longer served by the platform.** `research_report`
  now returns the cited `spec`, and `check_spec` returns the exact files.
- **`quant-factory serve-mcp --confirm-in-client`.** Creating a project is a terminal command.

### Changed

- **One command, `qaos`**, replaces `quant-agent-os`, `quant-research`, `quant-factory`, and
  `quant-platform`: `ingest`, `search`, `strategy`, `data add|list`, `keys new`, `runs`, `serve
  [--http]`, `catalog`, `app ...`, and `hosted ...`. `serve` is the one way to start a server;
  `quant-research serve-mcp` and `serve-api`, `quant-factory serve-mcp`, and `quant-platform
  serve-http` are gone, and `--toolsets` serves any subset. `data add` takes its id from the file
  name unless given `--id`. `info` became `doctor`.
- **One wheel with extras** ([decision 0018](docs/decisions/0018-one-wheel.md)). The five
  distributions are now one, `quant-agent-os`, with extras `analytics`, `server`, `pdf`, `postgres`,
  `factory`, and `all`. Sources moved to `src/` and tests to `tests/`; import names did not change.
- **One home.** `QUANT_AGENT_OS_HOME` (default `~/.quant-agent-os`) holds `config.toml`, `.env`,
  `knowledge.db`, `datasets/`, `runs.db`, `api-keys.toml`, and `factory.toml`, and every command
  uses it without flags. Factory runs join the one run store, replacing `factory-runs.db`.
- **`.env` is read only from the home, never the working directory**, which a client that launches
  the server chooses. `config.toml` may set paths and the log level, and refuses permissions: those
  come only from the environment or the home's `.env`.
- **One environment prefix.** `QUANT_RESEARCH_SCHEMA` and `QUANT_RESEARCH_EMBEDDING_TIMEOUT` are now
  `QUANT_AGENT_OS_*`. `QUANT_RESEARCH_VOYAGE_API_KEY`, `QUANT_RESEARCH_OPENAI_API_KEY`, and
  `QUANT_FACTORY_SETTINGS` are gone: use `VOYAGE_API_KEY`, `OPENAI_API_KEY`, and the home.
- **Hosted features moved to `qaos hosted`** ([decision 0017](docs/decisions/0017-hosted-behind-a-command-group.md)):
  `hosted serve` takes `--tenants`, `--approval-url`, and `--resource-url`, and `tenant` and
  `usage` are under `hosted`. The default `qaos serve --http` serves one team with API keys. Their
  modules moved from `quant_agent_os` to `quant_platform.hosted`. See [hosted.md](docs/hosted.md).
- **App Factory is off by default on the server**, and the plugin no longer installs it. Add it with
  `--toolsets research,analytics,factory`. Its commands are under `qaos app`.
- **The `quant-strategy` and `quant-app-factory` skills need server 0.2.0**, because they rely on
  `research_report` returning `spec` and `check_spec` returning the exact files.

### Added

- **`qaos init [--sample]`** makes the home and can load a sample corpus and dataset, now in the
  wheel. **`qaos connect claude|cursor|vscode`** prints a client's config, or with `--write` adds
  it to the project's file. **`qaos doctor`** checks the home, store, datasets, extras, and
  permissions, and says how to fix what is off.
- `qaos serve` takes `--embedder` and `--embedding-model`, so vector search works on the one server.
- `Platform.report(goal, calls)` runs named calls as one recorded run and returns the
  evidence-linked report. `examples/your-own-model` drives the tools from your own model.

## [0.1.2] - 2026-11-05

A correctness release: analytics could report wrong numbers with no warning. **If you ran
`analyze_performance`, `analyze_risk`, or `research_report` on daily or weekly data without passing
`periods_per_year`, or compared datasets dated like `20240102`, please recompute those results.**

### Fixed

- **Analytics no longer change a number silently.** `analyze_performance`, `analyze_risk`, and
  `research_report` used to annualise any series as monthly unless told otherwise, so daily data
  reported a Sharpe ratio about 4.6 times too low, with no warning. They now infer the frequency
  from the dataset's dates, warn when a given value disagrees, and refuse to guess when there are
  no dates. **Calls with inline values must now pass `periods_per_year`.**
- **`YYYYMMDD` and `YYYYMM` dates are dates.** They were read as a numeric column, so datasets
  skipped the alignment check, and a benchmark one row off computed a wrong beta with status `ok`.
- **Series from different datasets are joined on their dates**, and the rows left out are
  reported, instead of the call being refused for any difference.
- **Files with gaps load.** Blank and `NaN` cells are kept as missing, and the rows are left out of
  a calculation and counted. Nothing is filled in. Repeated dates and dates out of order are now
  refused, because returns compound in row order.
- Returns that look like percentages get a warning.

### Added

- `quant-agent-os dataset push --index COLUMN` names the date column, and the command reports the
  frequency the dates show. `list_datasets` reports it too.
- A hands-on [tutorial](docs/tutorial.md): eight labs with flow charts, sample data, things to
  try, and a feedback template. Its files are in `examples/tutorial/`.

## [0.1.1] - 2026-11-03

A security release for remote servers: two of the security review's findings are fixed (F-1
and F-5), and F-2 to F-4 remain open ([docs/security-review.md](docs/security-review.md)). No
tool, schema, or storage changes.

### Security

- **Internal errors no longer reach callers** (security review F-5). A tool that raises
  `ValueError` or `LookupError` still returns its message, because tools raise those for
  requests the caller can fix. Any other exception, and `KeyError` or `IndexError`, returns
  "failed with an internal error" with the call id. The exception text is kept in the run
  record's `error_detail` for the operator; `GET /v1/runs/{id}` leaves it out.
- **Introspection is bounded** (security review F-1), so unauthenticated requests cannot make
  the server flood a tenant's identity provider. A provider's refusal is cached for 30 seconds,
  and each provider gets at most 600 introspections a minute and 8 at once
  (`IntrospectionVerifier(introspections_per_minute=, max_concurrent=)`); beyond that a token
  is refused without asking.

## [0.1.0] - 2026-11-03

The first release (MVP), for local use and self-hosted team servers. All five packages ship at
0.1.0, and every tool at 1.0.0 ([tools changelog](docs/tools-changelog.md)). Known
limitations: a public, multi-tenant endpoint needs the steps in
[docs/security-review.md](docs/security-review.md) first (open findings F-1 to F-5), and the
model-provider features (B-12, B-15, B-16, B-20) are not in this release.

### Changed

- The run store is at schema 5 (usage events, B-36); older stores upgrade in place.
  `RunRecorder.resume` takes the original steps (B-35). `RunStore.add_call` takes the call's
  usage event, and `RunRecorder` takes `toolsets` (tool name to toolset).
- Calls have a new status, `quota_exceeded`, with `retry_after_seconds` on the record, in
  `failure_details`, and in the REST response (B-36). `ToolExecutor` takes `usage` (a
  `UsageGate`) and `max_output_bytes`; `RunStore` has `count_billable`.
- The team Docker image serves the platform (`quant-platform serve-http`: research and
  analytics over MCP and REST) instead of `quant-research serve-api`, with a read-only
  `datasets` volume and an operator `datasets` service. The platform's App Factory toolset is
  now an optional extra, `quant-agent-os-platform[factory]`, and is not in the image (B-33).
- `apps/quant-ai-assistant`, a superseded placeholder, is removed; `quant-factory`'s
  settings-configured factory is public as `default_factory()` (B-31).
- The agent surface costs clients less context (B-30, [plan](docs/plans/agent-surface.md) P1):
  - **Leaner definitions.** `tools/list` and `GET /v1/tools` drop the `title` keywords pydantic
    derives from names, and Quant Analytics serves only `analyze_performance` and
    `analyze_risk`: `annualized_return` and `max_drawdown` remain importable
    (`SINGLE_METRIC_TOOLS`) but are not in `analytics_tools()`. Definition bytes per turn fall
    20%, from 10 tools to 8.
  - **Concise search.** `find_related_signals` takes `detail` (`concise` by default, or
    `full`). Concise returns 300-character previews and no edges repeated from an entity's
    path; rankings, entities, and evidence are unchanged.
- Quant App Factory asks less and prints less (B-29):
  - **One-command start.** `quant-factory new quant-research my-lab --defaults` writes the spec
    and creates the project after one confirmation; add `--yes` for scripts.
  - **Only questions that apply.** The database component asks only for the secrets its kind
    uses (none for SQLite or DuckDB), and components are a numbered menu.
  - **Quiet output.** `plan` and `create` print a summary by default; `--verbose` lists the
    reasons, dependencies, and every file.
  - **One way to name a target.** Docs and `new` use `template` and `profile`, `kind` remains
    a documented shortcut, and every documented spec is checked in the tests.
- Quant baseline projects are now one `quant-research` project (B-28 S1, quant-baseline
  2.0.0):
  - **Commands.** `study`, `backtest`, and `report` subcommands, plus `check-connection` with
    the database component. These replace three separate projects and the `--check-connection`
    flag.
  - **Parameters.** Research parameters live in a generated `config.toml`, and flags override
    them for one run.
  - **Old names.** `kind = "alpha-research"`, `"backtest"`, or `"report"`, and those profile
    names, still work for one release: they build `quant-research` and warn.
  - **Prompts.** `quant-factory new` skips the profile question when a template has one
    profile.
  - **Tools.** Projects with `agent-tools` offer `evaluate_signal`, `backtest_signal`, and
    `render_report` together.
- Quant research runs are experiments (B-28 S2):
  - **One folder per run.** Each `study` or `backtest` writes its own folder under `results/`
    and never overwrites another.
  - **Provenance.** `results.json` (version 2) records a SHA-256 fingerprint of the price data,
    the code version, and the time.
  - **Parameter snapshot.** A `config.toml` in the folder holds the exact parameters, and
    `--config` pointing at it repeats the run.
  - **Report.** `report` defaults to the latest run and shows the provenance. Runs print a
    one-line headline, and `--out` is replaced by `--results DIR` and `--label`.
- Quant research projects have a `walk-forward` command (B-28 S3). It chooses the lookback
  per fold on training data only, from a small grid, by net Sharpe or rank IC, and reports
  out-of-sample results only, with a folds table.
- Quant research backtests take an `exposure` (B-28 S4): `dollar-neutral` (default),
  `beta-neutral` (zero beta to the equal-weight market, rolling betas), or `long-only` (top
  quantile, with information ratio and tracking error against the equal-weight market). Every
  backtest reports its realised beta.
- Quant research reports compare runs (B-28 S5): `report RUN ...` puts parameters, metrics,
  calendar-year returns, and provenance side by side, and every report with a return series
  gets an SVG equity curve and a calendar-year table.
- Quant research projects read their data from `config.toml` (`data = "demo"`, `"database"`,
  or a price file), so commands need no data flag (B-28 S6). Agent tools (`evaluate_signal`,
  `backtest_signal`, `walk_forward_signal`) read that data instead of taking inline prices,
  take no paths, return metrics without series, and declare `network` for PostgreSQL.
- `quant-factory` records runs in `factory-runs.db` beside `factory.toml`, so
  `QUANT_FACTORY_SETTINGS` moves both.

- Serving `find_related_signals` with a hosted embedder now needs `network` both granted and
  pre-approved (`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`), because each `network` call needs
  an approval.
- Python 3.15 is supported and tested in CI (3.11–3.15 on Linux, macOS, and Windows).

### Fixed

- Quant baseline studies now use the backtest's trade timing (B-27 R1). A study scored a
  signal against the return starting at the signal's own close, while the backtest trades one
  bar later, so a signal with a one-bar edge looked strong in the study (rank IC 0.27) and
  earned nothing in the backtest. Studies, IC decay, and quantile spreads now measure returns
  from the trade, using `config.TRADE_LAG`; results record the lag, and both CLIs take
  `--lag`. Regenerate existing projects, or re-run studies, to get comparable numbers.
- Quant baseline backtests and studies no longer drop delisting losses (B-27 R2). A held
  asset whose prices stop now earns `config.DELISTING_RETURN` (default -30%) once instead of
  0, and studies count it instead of dropping the asset. Positions are carried through price
  gaps, which settle at the price ratio when prices resume. Results count forced exits.
- Quant baseline backtests rebalance on a schedule and let holdings drift (B-27 R3).
  `config.REBALANCE_EVERY` (21 bars for daily data, 4 weekly, 1 monthly; `--rebalance-every`)
  sets the schedule. Trades are the target minus the drifted holdings, so costs now include
  the drift that a constant-weight book must trade back, which was modelled as zero. Results
  add `annual_turnover`.
- Quant baseline IC t-statistics use Newey-West (HAC) standard errors (B-27 R4), with at
  least horizon - 1 lags, so overlapping forward returns no longer inflate them. IC decay
  reports a t-statistic at each horizon (`ic_decay_mean_rank_ic`, `ic_decay_t_stat`), which
  replaces the `ic_decay` series.
- Quant baseline loaders parse dates (B-27 R5). CSV, Parquet, and database dates become
  `datetime.date`, so order is checked as dates rather than text. Dates must be ISO
  (`2024-01-09`) and unique; other formats are refused with an example. Synthetic panels use
  weekdays, and the price tools take optional `dates` (warning when they are missing).
- Quant baseline signals default to the standard definitions (B-27 R6): 12-1 momentum
  (a year's return, skipping the latest month) and one-month reversal, scaled to the data's
  frequency. Momentum takes `skip` (`--skip`), results record the parameters used, and bad
  parameters or data are reported as CLI errors rather than tracebacks. The old momentum
  default (20 bars, skip 1) is `--lookback 20 --skip 1`.
- Quant Research on real papers (measured on 21 arXiv papers, `docs/plans/real-corpus-eval.md`):
  - Searches for things the corpus never mentions now return no passages instead of
    passages that share a few common words.
  - Typed relations are no longer read from tables, reference lists, run-on sentences, or
    entity types a predicate cannot join. Labelled precision rose from 0.30 to 0.75.
  - PDF text has ligatures expanded and line-break hyphenation undone, so terms such as
    "momentum" match across line breaks.
  - Stores re-process on the next ingest, because the extractor (`rules-v5`), chunker
    (`page-v2`), and PDF loader (`pdf-v2`) versions changed.
- Serving and searching open the knowledge store read-only, so they never migrate or write it,
  and a read-only mount works. Answers read one consistent corpus state. Snapshots are built
  and published atomically. The schema migration is atomic and can be re-run.
- Hosted query embedding has a deadline inside the tool timeout and degrades to the other
  channels on failure. Repeated queries are cached instead of sent to the provider again.
- MCP: `openWorldHint` is also set for `external_action` tools, and a missing `mcp` extra
  gives the install hint instead of a traceback.

### Added

- Metering (B-36, [decision 0015](docs/decisions/0015-metering-quotas-and-entitlements.md)
  P7.1):
  - **A usage event per call.** Every recorded call gets one, in the same transaction: tenant,
    principal, interface, tool and version, toolset, status, duration, and input and output
    bytes, never arguments or outputs. `ok`, `error`, and `timeout` are billable.
  - **Usage export.** `quant-platform usage export [--tenant T] [--from D] [--to D]
    [--by day|tool] [--format csv|jsonl]`, an operator command; the team compose file has a
    `usage` service for it.
- Plans and limits (B-36, decision 0015 P7.2):
  - **Plans in the tenants file.** `[plans.<name>]` sets `toolsets`, `calls_per_minute`,
    `monthly_calls`, and `max_output_bytes`; a tenant names its `plan`
    (`quant-platform tenant add --plan`). Out-of-plan tools are not listed.
  - **Checked before a call starts.** A refused call records `quota_exceeded` and is not
    billable; REST answers 429 with `Retry-After`. The rate limit is per process; the monthly
    quota counts billable usage events.
  - **Approval page limits.** Five failed submissions deny a request; each client address may
    submit 30 forms a minute.
- Tool compatibility (B-36, decision 0015 P7.3; [tools changelog](docs/tools-changelog.md)):
  - **Semver tool versions.** Every tool is `1.0.0` (was `"1"`); `@tool(version=...)` must be
    `MAJOR.MINOR.PATCH`. `Deprecation` marks a tool's replacement and removal date, shown in
    its description and in MCP `_meta` (`qaos/version`, `qaos/deprecated`).
  - **A tool lock.** `tool-versions.lock` records each served tool's version, permissions, and
    schemas; the tests fail when a schema changes without a big enough bump
    (`quant_agent_os.tools.compatibility`).
  - **Server info.** The `server_info` tool and the `qaos://server/info` resource give the
    platform version, every tool's version and deprecation, and the registry digest. MCP
    `resources/list` lists resources without variables. Each skill checks the major.
  - **One version.** Every package is released at the same version.
- Operations (B-36, decision 0015 P7.4):
  - **OpenTelemetry per call.** A span `execute_tool <tool>`, the counter `qaos.tool.calls`,
    and the histogram `qaos.tool.duration`, labelled by tenant, tool, and status, never
    arguments, outputs, callers, or keys. Only the API is used, so it does nothing until an
    operator configures an SDK; `opentelemetry-api` is now a direct dependency of the `mcp`
    and `api` extras. `ToolExecutor` takes the `tenant` it serves.
  - **Service levels.** Availability and p95 latency per tool in
    [deployment](docs/deployment.md), with an example Grafana dashboard in
    `deploy/observability/`.
  - **Security review.** [docs/security-review.md](docs/security-review.md) checks the remote
    surface against the OWASP API Top 10 and the MCP security best practices, with five open
    findings and the steps before a public endpoint.
- Skills and client packaging (B-32, [plan](docs/plans/agent-surface.md) P3):
  - **Skills.** Four instruction-only skills in `skills/`: `quant-research`,
    `quant-analytics`, `quant-strategy`, and `quant-app-factory`. They teach cited search,
    data by reference, numbers quoted as computed, cited strategy specs and reports, and App
    Factory specs created locally. Each states the minimum server version it needs. A test
    checks that every name a skill uses is served, and that every served tool, prompt, and
    resource is taught.
  - **Claude Code plugin.** `claude plugin marketplace add yuxuzi/quant-agent-os`, then
    `claude plugin install quant-agent-os@quant-agent-os`, installs the platform server and
    the skills.
  - **Cursor and VS Code (Copilot).** `clients/` holds the same server command with the
    skills as a rules file, generated from the skills.
  - **The platform server finds its store.** It reads `QUANT_AGENT_OS_STORE` or
    `~/.quant-agent-os/knowledge.db`, and starts without the research tools, with a warning,
    when there is none.
  - **Efficiency.** The agent-efficiency evaluation counts the skills: 1,114 bytes of
    descriptions per turn, with calls and success unchanged.
- Approving without leaving the client (B-35, [decision 0014](docs/decisions/0014-approvals-in-the-client.md)):
  - **Approval page.** With `quant-platform serve-http --approval-url URL`, a call that waits
    for an approval sends the MCP client to `/approve/<id>` (URL elicitation). An approver
    key (new scope `runs:approve`, never combined with `tools:call`) approves it once, and
    the retried call runs in its original run. Four eyes outside the `local` tenant,
    configurable with `separate_approver`.
  - **Local confirmation.** `quant-factory serve-mcp --confirm-in-client` serves
    `create_app` and `check_app`, and the client asks the user to confirm each call.
- Tenants and OAuth (B-34, [decision 0013](docs/decisions/0013-identity-and-tenancy.md)):
  - **Many tenants on one server.** `quant-platform serve-http --tenants FILE` serves each
    caller its own tenant: its knowledge store (a PostgreSQL schema or SQLite file), datasets,
    toolsets, and permissions. `quant-platform tenant add/list/disable` manage the file.
  - **The tenant comes from the credential.** `api-key new --tenant`, an OAuth token's tenant
    claim; runs record the tenant, and `/v1/runs` shows only the caller's.
  - **OAuth 2.1 for MCP connectors.** Tokens from the identity providers listed in the tenants
    file are checked by introspection: active, audience, pinned issuer, and tenant. The server
    publishes the Protected Resource Metadata, and its 401s point to it (`--resource-url`).
  - **Operator commands per tenant.** `quant-research ingest/search --schema` and
    `quant-agent-os dataset push/list --tenant`.
- Remote MCP (B-33, [decision 0012](docs/decisions/0012-remote-mcp-over-http.md)):
  - **`quant-platform serve-http`** serves MCP at `/mcp` and REST at `/v1` on one port, with
    the same API keys, policy, and run store. Add it to Claude Code with
    `claude mcp add --transport http <url>/mcp --header "Authorization: Bearer <key>"`.
  - **Stateless Streamable HTTP** with JSON responses. A missing or unknown key gets 401; each
    MCP method checks the key's scope (`tools:list` or `tools:call`); calls are recorded with
    actor `mcp-http` and the key's principal; DNS-rebinding protection admits loopback and
    `--allowed-host` names. `quant_agent_os.mcp.http.add_mcp_route` adds it to any app.
- One MCP server for every toolset (B-31, [plan](docs/plans/agent-surface.md) P2):
  - **Platform server** (`apps/quant-platform`): `quant-platform serve-mcp` serves the
    research, analytics, and factory toolsets (`--toolsets` narrows them), and
    `quant-platform catalog` lists what a client will see.
  - **Resources and prompts.** `ResourceSpec` (a URI template read through a tool, with that
    tool's permissions, limits, and run record) and `PromptSpec`. Resources:
    `qaos://passage/{chunk_id}`, `qaos://strategy/{focus}`, `qaos://dataset/{dataset_id}`,
    and `qaos://template/{template}[/{profile}]`. Prompts: `research_signal`,
    `report_on_signal`, `analyze_returns`, and `draft_app_spec`.
  - **Datasets by reference.** `quant-agent-os dataset push FILE --id ID` adds a CSV table to
    `QUANT_AGENT_OS_DATASETS_DIR`; `list_datasets` describes them; with a dataset store,
    `analyze_performance` and `analyze_risk` take `{dataset, column}` references, and each
    dataset's fingerprint becomes a data version. Analysing a year of daily returns now costs
    the model about 125 argument bytes instead of 3,750.
  - **`research_report`**: a cited, evidence-linked report on a signal, with metrics of a named
    series, in one call. A fixed plan, no model.
- Typed, actionable results for agents (B-30 P1):
  - **Output schemas.** Every MCP tool definition carries `outputSchema` and a `title`; clients
    validate `structuredContent` against it, and `check_tool` does too.
  - **Failure hints.** A failed call reports `status`, `error`, `hint` (what to do next), and
    `retryable`, as MCP `structuredContent` and in the REST call response
    (`quant_agent_os.runtime.failure_details`).
  - **Untrusted text.** Research results that carry corpus text say `"trust": "untrusted"`,
    and the research server tells clients never to follow instructions in passages.
  - **Agent-efficiency evaluation** (`tests/evaluation/agent_efficiency.py`): definition,
    argument, and result bytes, calls, and success for typical tasks over the MCP servers,
    with a baseline the default suite enforces.
- A PostgreSQL knowledge store with pgvector (B-14, the `postgres` extra with
  `psycopg[binary]`; [decision 0004](docs/decisions/0004-knowledge-store.md)).
  - **Usage.** `--store postgresql://user@host/db` works with every `quant-research` command,
    and `open_research` takes the URL too.
  - **Same answers.** It gives SQLite's answers for the same corpus: keyword search is FTS5's
    BM25 computed in SQL, and ties break in the same order.
  - **Contract tests.** They run every case on both stores against an SQLite reference. CI
    runs them against `pgvector/pgvector:0.8.6-pg17`.
  - **Passwords** come from `PGPASSWORD` or `.pgpass` and are redacted from every message.
  - **pgvector is optional.** Without it, the vector channel reports unavailable.
- The team server runs on PostgreSQL too (`deploy/compose.postgres.yaml`).
  - **Roles.** The server connects as a role that can only read, and ingestion connects as
    the schema's owner.
  - **Network.** PostgreSQL sits on an internal network.
  - **Smoke test.** It covers both stores, and checks that the server's role is refused
    writes by permission.
- A Docker team server (B-19, [docs/deployment.md](docs/deployment.md)).
  - **The image.** `deploy/Dockerfile` builds an image that runs `quant-research serve-api`
    as a non-root user. It holds code only: the locked dependencies of Quant Research with the
    `api` and `pdf` extras, on Python and uv base images pinned by digest.
  - **Compose.** `deploy/compose.yaml` mounts the knowledge store and API keys read-only and
    the run store writable. It runs with a read-only root filesystem and no capabilities,
    publishes on loopback only, and adds operator services for `ingest`, `keys`, and `runs`.
  - **CI.** A new job runs `deploy/smoke-test.sh` on every pull request. It builds the image,
    checks that the image holds no data or secrets, and brings the server up with `docker
    compose up`. It then checks authentication, a real call, and the principal on the
    recorded run.
  - **Storage.** It uses the SQLite store for now; PostgreSQL follows with B-14.
- A REST interface with scoped API keys (B-18, [decision 0011](docs/decisions/0011-rest-api-and-api-keys.md);
  optional `api` extra with FastAPI and uvicorn):
  - **Serving.** `quant-research serve-api` serves the research tools over HTTP on
    `127.0.0.1:8000` by default. It lists tools, calls them, and reads runs, through the same
    executor and run recorder as MCP, so a REST result equals the MCP result.
  - **Keys.** `quant-agent-os api-key new --principal alice --scope tools:call` prints a key
    once and stores only its SHA-256 hash, in `~/.quant-agent-os/api-keys.toml` or
    `QUANT_AGENT_OS_API_KEYS_FILE`. The scopes are `tools:list`, `tools:call`, and
    `runs:read`. They narrow access and never widen the permission policy, which stays
    read-only by default.
  - **Audit.** Every REST call is a run with actor `rest` and the key's principal, on the run
    and on every audit event (run-store schema 3; older stores upgrade in place).
  - **Defaults.** The server does not start without an enabled key or a run store, offers no
    route to approve calls, publishes no schema pages, caps request bodies at 5 MiB, and warns
    when bound beyond loopback.
- `analyze_risk` in Quant Analytics (B-21). From a portfolio's weights and its assets' returns,
  it returns annualised volatility and each asset's marginal, component, and percent
  contribution to it. The contributions are Euler's, so they add up exactly, and a negative
  share marks a hedge. It also returns gross and net exposure and the diversification ratio.
  With factor returns, it adds OLS factor exposures for the portfolio and each asset, and the
  share of portfolio variance each factor explains, with the specific share as the remainder.
  Collinear factors and short samples are flagged. empyrical has no risk decomposition, so the
  formulas use numpy; known-answer tests pin every convention, and the single-factor beta
  matches empyrical's. Like `analyze_performance`, it needs no permissions.
- `generate_strategy` in Quant Research (B-17). It drafts a structured strategy specification
  for one signal, factor, or strategy from the user's corpus. The spec has sections for
  signals, universe, data, methods, metrics, risks, and context. Risks are what contradicts
  or explains away the focus. Every item cites the passages that state it, and anything the
  corpus leaves undecided (parameters, a missing universe or dataset) becomes an open question
  rather than a default. The spec is not code and is not built by a model, so the same corpus
  snapshot always gives the same spec. The tool is read-only; `quant-research strategy
  "trend following" --json` prints the same spec locally.
- Quant Analytics (`packages/quant-analytics`, B-13; decision 0010). The `analyze_performance`
  tool returns a series' performance in one read-only call. Without a benchmark it gives
  annualised return and volatility, Sharpe, Sortino, Calmar, Omega, maximum drawdown, tail
  ratio, stability, value at risk, and hit rate. With a benchmark it adds alpha, beta, active
  return, tracking error, and information ratio. Each result carries its conventions,
  warnings, and computed evidence. The formulas are empyrical-reloaded's, pinned by
  known-answer tests; pyfolio was considered and not used, because it adds only plotting at
  more than twice the footprint. The example `annualized_return` and `max_drawdown` tools now
  come from this package.
- A quant review of the templates and App Factory (`docs/plans/quant-review.md`).
  - **Measured flaws.** The study and the backtest use different trade timing, delisted
    positions lose nothing, and costs ignore drift.
  - **Plan.** Fixes in three tested phases, B-27 to B-29: correct numbers, then one project
    with out-of-sample results, then less to decide.

- App Factory review fixes:
  - **Connection check.** It runs only for built-in templates, and redacts URL-encoded forms
    of secrets too.
  - **TLS.** Generated PostgreSQL connections verify the server certificate by default
    (`sslmode=verify-full`).
  - **Pack drift.** The approval for `create_app` binds a digest of the previewed pack, and a
    pack changed after its preview is refused.
  - **Retries.** `quant-factory check` (the `check_app` tool, approved per call) re-runs an
    existing project's checks.

- Template forks and registered packs (B-26):
  - `quant-factory template fork` copies a built-in profile into your own pack, optionally
    renaming it, then checks and registers it.
  - `template check` loads a pack and plans every profile.
  - `template add` registers a local pack, or a git pack pinned to a tag and cached locally.
  - `template list` shows the built-in and registered templates.
  - Specs can name a registered template.
  - Walkthrough §11: settings, questionnaire, study, backtest, report, and fork, on database
    prices.

- Database guidance (B-25):
  - **The `data-database` component** of `alpha-research` and `backtest` loads prices with
    one read-only query from PostgreSQL, SQLite, or DuckDB.
  - **The generated app** reads credentials only from `DATABASE_URL` or `DATABASE_PASSWORD`,
    ships a `.env.example`, and has a `--check-connection` flag.
  - **Saved connections.** Specs can name a connection saved in `factory.toml`.
    `quant-factory new` offers saved connections and saves new ones.
  - **The factory's check.** `quant-factory create --check-connection` passes only the
    referenced variables and redacts them from the output.

- Guided specs and factory settings (B-24; decisions 0007 and 0008 accepted):
  - **Read-only tools.** `describe_template` lists a template's profiles, components,
    questions, and runtime settings, with secret ones marked. `check_spec` reports `ready` with
    the exact files and the spec as TOML, or every problem.
  - **Commands.** `quant-factory new` asks a template's questions in the terminal.
    `quant-factory serve-mcp` serves the guiding tools to Claude (not `create_app`).
  - **Factory settings.** `quant-factory settings` edits `~/.quant-agent-os/factory.toml`:
    identity, plus connections that hold environment-variable names, never secrets.
  - **Specs.** `AppSpec.settings` holds runtime settings as values or `env:NAME` references.
    Author, email, and license default from the settings file, then `git config`.

- Quant baseline templates (B-23):
  - **Profiles.** App Factory's `quant-baseline` pack has `alpha-research`, `backtest`, and
    `report`, with the kinds of the same names. The projects are portable numpy projects with
    one `metrics` module and known-answer tests.
  - **Bias tests.** Every project checks for look-ahead, the trade lag, and cost ordering, and
    carries a `LIMITATIONS.md`.
  - **Components.** Optional `data-parquet` and `agent-tools`.
- **App specs.** `AppSpec` accepts `template` (a built-in pack, `bundled`, or the path of
  your own pack), `profile`, and `answers`. `kind` stays a shortcut.

- Planned quant templates and a guided App Factory (`docs/plans/app-factory-questionnaires.md`,
  backlog B-23–B-26, proposed decisions 0007–0009):
  - `quant-baseline` templates (`alpha-research`, `backtest`, `report`), with bias tests;
  - user packs;
  - stateless guidance (`describe_template`, `check_spec`, `quant-factory new`);
  - a factory settings file, with secrets as environment-variable names only;
  - database setup;
  - template forks.
- Approval gate (B-09, decision 0005 accepted).
  - **Gate.** `write_local_data`, `execute_code`, `network`, and `external_action` calls need
    a single-use operator approval, bound to the run, the tool, and the input hash. Without
    one, the call records `approval_required`.
  - **Operator path.** `Platform.approve` and `Platform.resume`.
  - **Standing approvals.** `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`, empty by default.
  - **Run store.** Schema 2 adds approvals and upgrades version 1 in place.
- `quant_agent_os.runtime.runner.run_command`: run programs with an allowlisted environment
  and a timeout that kills the process tree.
- `quant_agent_os.testing.check_tool`: the platform's contract checks, for tool packages'
  own tests.
- Quant App Factory foundation (B-10):
  - `AppSpec`, `ArchitecturePlanner`, and the `ScaffoldProvider` port;
  - `PytemplateUvScaffolder`, which uses the pytemplate-uv library (`pytemplate.api`,
    `pytemplate-uv>=3.0,<4`; decision 0006);
  - an `agent-tool` template pack;
  - the `plan_app` tool (read-only, with the exact file preview) and the `create_app` tool;
  - `quant-factory plan` and `quant-factory create`.

- Run persistence (B-08): `quant_agent_os.runs` with in-memory and SQLite run stores, a
  `RunRecorder`, append-only audit events, and redaction of secrets.
  - The orchestrator and the MCP adapter record runs when given a recorder.
  - `quant-research serve-mcp` records every call to `runs.db` (`--runs`, `--no-record`).
  - `quant-research runs` lists runs and shows one run in full.
- SDK facade (B-07): `quant_agent_os.sdk.Platform` with `call`, `run`, `get_run`, and `runs`,
  producing the same records as the MCP path.
- A walkthrough (`docs/walkthrough.md`) from a clean checkout to cited answers in Claude Code
  and Claude Desktop, with recorded runs and known limitations, plus an SDK example
  (`examples/quant-research/sdk_demo.py`).
- An opt-in real-paper evaluation (`packages/quant-research/tests/real_corpus.py`) over 21
  pinned arXiv papers, with gold questions and hand-labelled relations.
- Graph RAG answers carry `path`, the cited chain of edges from the query entity, and the
  `quant-research search` CLI shows it with direction. Graph expansion aggregates parallel
  edges into a `support` count.
- A mock world with an answer key (`tests/mock_world.py`), a graph evaluation
  (`tests/graph_eval.py`, baseline `graph_baseline.json`), and an opt-in scale benchmark
  (`tests/research_bench.py`).

### Fixed

- Extraction (`rules-v4`): negated statements no longer become relations, passive voice no
  longer reverses edges, and "X explains Y" is extracted as `Y explained_by X`. Negation counts
  only right before the relation cue, so "X, which is not small, predicts Y" and "not only"
  keep their relation.
- Ingestion re-processes unchanged files when the pipeline (loader, chunker, extractor, or
  vocabulary version) changes, reported as `reprocessed`. This is schema version 3 and is
  migrated in place.
- Graph paths: equally short paths are chosen by total confidence, independent of seed order.
  Excluding the `vector` channel skips query embedding. `GraphEdge` requires
  `support >= 1`.
- Two-hop expansion no longer slows down with repeated facts: 1.1 s to 14 ms p50 at 3,000
  documents.

- MCP server over stdio: `quant_agent_os.mcp.server` (optional `mcp` extra, built on the
  official MCP Python SDK) forwards `tools/list` and `tools/call` to the existing adapter.
  `quant-research serve-mcp --store PATH` serves the read-only research tools to Claude Code,
  Claude Desktop, and other MCP clients. Tool annotations (read-only, idempotent, open-world)
  are derived from declared permissions.

- uv workspace: Core stays at the root; `packages/quant-research` and
  `packages/quant-app-factory` (skeleton) are members, and a contract test enforces the import
  boundaries.
- Run provenance: `call_id`, `run_id`, `started_at`, `input_sha256`, and `data_versions` on
  every `ToolCallRecord`; `run_id`, `registry_digest`, and `data_versions` in `RunMetadata`;
  `deterministic` and `idempotent` flags on `ToolSpec`.
- Quant Research:
  - Markdown and text ingestion with front matter;
  - the bundled `quant-v1` vocabulary and rule-based extraction;
  - a SQLite knowledge store (FTS5, recursive-CTE graph expansion);
  - hybrid keyword, mention, and graph retrieval with rank fusion;
  - evidence packets that cite every passage and graph edge;
  - the read-only tools `find_related_signals` and `get_source_passage`;
  - the `quant-research` CLI;
  - a retrieval-quality evaluation with a recorded baseline;
  - the `examples/quant-research` demo;
  - an optional vector channel (`Embedder` and `VectorIndex` ports, an offline
    `HashingEmbedder`, exact cosine search in SQLite), off by default after a measured
    no-gain result; `--embedder hashing` enables the mock;
  - hosted embedding models over HTTPS with no new dependency: `VoyageEmbedder` (default
    `voyage-finance-2`) and `OpenAIEmbedder`, chosen with `--embedder voyage|openai` and
    `--embedding-model`. Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` as secrets, and
    query embedding makes `find_related_signals` declare the `network` permission;
  - PDF ingestion with page-level citations through the optional extra
    `quant-agent-os-research[pdf]`, with scanned and corrupt PDFs reported;
  - knowledge-store schema version 2 with in-place migration.

### Changed

- Repositioned the project as Quant-Agent-OS: a platform consumed by AI clients over MCP and the
  SDK, with two products, Quant Research (Graph RAG) and Quant App Factory. Rewrote
  `docs/architecture.md` and `docs/ENGINEERING_REVIEW_PLAN.md` (gap analysis, MVP, 30- and 90-day
  roadmap, backlog). Removed the uv-template planning content; `pytemplate-uv` 3.0 is used as an
  external tool.
- Added proposed decision records 0003 (brand and product structure), 0004 (knowledge store),
  and 0005 (approval gates). Marked the Quant AI Assistant placeholder as superseded.

## Foundations (developed before 0.1.0, and part of it)

### Changed

- CI and release workflows use Node.js 24 actions (`actions/checkout@v7`,
  `astral-sh/setup-uv@v10.3.2`, `actions/upload-artifact@v7`, `actions/download-artifact@v8`).

### Added

- A tool contract (`ToolSpec`, `@tool`) with typed pydantic input and output, declared
  permissions, timeouts, and output-size limits; an explicit `ToolRegistry`; and a read-only-by-
  default `PermissionPolicy`.
- `ToolExecutor`, which records every call (ok, invalid input, denied, unknown tool, timeout,
  error) with its version and duration.
- A single bounded `Orchestrator`: provider-proposed plans are validated against the registry,
  allowlist, and step limit before execution.
- `Evidence` and `SourceRef` models that keep computed, retrieved, and interpretation statements
  distinct; structured `Report` output with Markdown rendering.
- A `ModelProvider` protocol and `FakeModelProvider`; `QUANT_AGENT_OS_*` settings with secret
  handling; log redaction.
- An MCP adapter (`tool_definitions`, `call_tool`) over the same executor.
- The `quant-agent-os` CLI: `info`, `plan-schema`, `--version`.
- An offline `examples/portfolio-research` workflow on synthetic data.
- Architecture, configuration, security, and getting-started docs; decision records 0001 and
  0002; and the QuantFoundry engineering plan, moved here from pytemplate-uv.
