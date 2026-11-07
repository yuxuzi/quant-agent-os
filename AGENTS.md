# AGENTS.md

Instructions and context for AI coding agents working on **quant-agent-os** (Quant-Agent-OS).
Claude Code reads this file through `CLAUDE.md`.

## What this project is

Quant-Agent-OS connects AI clients to a firm's own Python tools, research, and data, on a server the firm runs, through
MCP, REST, and a Python SDK. It is source-available ([decision 0019](docs/decisions/0019-business-source-license.md)), and it
is not a chatbot.
- **Core** (`src/quant_agent_os`): typed tool contract and registry, permission policy, bounded
  executor, evidence-linked reports, and the MCP adapter.
- **Quant Research** (`src/quant_research`): cited keyword search with entity linking and a small
  graph of extracted facts, plus optional semantic search. It is layered as `domain` → `application` → `infrastructure`, with `tools.py` and `cli.py` as interfaces and
  `bootstrap.py` as the composition root.
- **Quant App Factory** (`src/quant_app_factory`): Python application generation. An
  `AppSpec` goes through the `ArchitecturePlanner` and a `ScaffoldProvider` (pytemplate-uv),
  and the generated project's checks run through the subprocess runner. Its `agent-tool`
  template pack is in `quant_app_factory/packs/`.
- **Quant Analytics** (`src/quant_analytics`): performance metrics from empyrical-reloaded
  ([decision 0010](docs/decisions/0010-analytics-on-empyrical.md)). `metrics.py` imports
  numerical libraries only; `tools.py` registers `analyze_performance` and friends.
- **Hosted features** (`quant_platform.hosted`): tenants, plans, usage, OAuth, and the approval page,
  run only through `qaos hosted` ([decision 0017](docs/decisions/0017-hosted-behind-a-command-group.md)).
- **Platform server** (`src/quant_platform`): the composition root. `compose()` assembles the
  chosen toolsets' tools, resources, and prompts, plus the deterministic `research_report` and a firm's
  own tools ([decision 0020](docs/decisions/0020-a-firms-own-server.md));
  `qaos serve` serves them as one MCP server, a firm's own server does so with `serve_http`,
  `open_platform()` opens them from a notebook, and `qaos eval` runs agreed test cases against them.
- **Skills and client packaging**: `skills/` holds the four instruction-only skills,
  `.claude-plugin/` makes the repository a Claude Code plugin (server plus skills), and
  `clients/` holds the generated Cursor and VS Code bundles
  ([plan](docs/plans/archive/agent-surface.md), P3).

Core must never import a product, and the products must not import each other. Only the
platform server imports every product, and nothing imports it. Quantitative
analytics live in a separate package and are registered as tools. `pytemplate-uv` is an
third-party library of App Factory ([decision 0006](docs/decisions/0006-pytemplate-uv-as-a-library.md)):
- Import only its public `pytemplate.api`, and only in
  `quant_app_factory/infrastructure/pytemplate_uv.py`, behind the `ScaffoldProvider` port.
- Core and Quant Research never import it.
- Never vendor it, and never plan changes to it here.

Read [docs/architecture.md](docs/architecture.md) and the open items in
[docs/ENGINEERING_REVIEW_PLAN.md](docs/ENGINEERING_REVIEW_PLAN.md) before larger changes. User docs
have one route: README, then [getting started](docs/getting-started.md), then the
[tutorial](docs/tutorial.md). Someone running a server reads [deployment](docs/deployment.md), then
[operations](docs/operations.md).

One wheel holds all five import names ([decision 0018](docs/decisions/0018-one-wheel.md)), and
`qaos` (`quant_platform.cli`) is the one command. Each product's `cli.py` is a Typer app it mounts,
and each `tools.py` holds what serving needs, so serving imports no command module. Importing
`quant_platform.cli` must work with no extra installed; a test checks that.

`uv` and `uvx` are the only required tools; never use pip, virtualenv activation, or make.

## Commands

| Task | Command |
| --- | --- |
| Install | `uv sync` |
| Test (unit, contract, integration) | `uv run pytest` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Type-check (strict, `src` and `examples`) | `uv run mypy` |
| Offline demos | `uv run python examples/portfolio-intelligence/demo.py`, `uv run python examples/portfolio-research/portfolio_research.py`, `uv run python examples/quant-research/research_demo.py`, `uv run python examples/quant-research/sdk_demo.py` |
| Agent-efficiency evaluation | `uv run python tests/evaluation/agent_efficiency.py` (add `--update` to rebaseline) |
| Regenerate client bundles (after changing a skill) | `uv run python scripts/client_bundles.py` |
| Update the tool lock (after a tool version bump) | `uv run python scripts/tool_lock.py --update` |
| Research evaluation | `uv run python tests/research/research_eval.py` (add `--update` to rebaseline) |
| Team-server smoke test and upgrade drill (need Docker; CI runs them) | `deploy/smoke-test.sh [postgres]`, `deploy/upgrade-test.sh [postgres]` |
| App Factory smoke test (opt-in; needs network) | `QUANT_FACTORY_SMOKE=1 uv run pytest tests/factory/test_factory_smoke.py` |
| Real-paper evaluation (opt-in; `fetch` needs network once) | `uv run python tests/research/real_corpus.py fetch`, then `... real_corpus.py eval` or `... real_corpus.py ablation` |

Before reporting work as done, run the test, lint, format, and type-check commands and report
the real results.

## Layout and layering

`src/quant_agent_os/`, importing downward only:
1. `tools/`: contract (`ToolSpec`, `@tool`), registry, permissions, per-caller access;
   `tenancy.py` (tenant ids).
2. `runtime/call.py`, `runtime/approvals.py`, `runtime/runner.py`, `runtime/executor.py`: tool
   calls, approvals, the subprocess runner, and bounded execution.
3. `evidence/`, `reporting/`, `runs/`: evidence, report models, rendering; the run store,
   `RunRecorder`, and audit. `runs/` reuses only the `REDACTED` marker from
   `observability/logging.py`.
4. `config/`, `data/` (datasets read by id), `serving.py` (tenant surfaces),
   `mcp/` (stdio, HTTP), `sdk/`, `testing.py` (`check_tool`), `observability/`,
   `cli.py`.

Tests live in `tests/unit`, `tests/contract` (every tool honours the contract; MCP equals a
direct call), `tests/integration` (end-to-end, offline), and `tests/evaluation`
(opt-in quality evaluations).

## Invariants

- **Clients orchestrate.** The platform has no planner and calls no model
  ([decision 0016](docs/decisions/0016-clients-orchestrate.md)). No multi-agent features.
- **Business logic stays out of MCP.** MCP code only adapts the registry
  ([decision 0002](docs/decisions/0002-mcp-boundary.md)).
- **Per-caller access fails closed.** With an access policy, no bound caller, a failed check, or an invalid
  file is a refusal, and a refused dataset looks like a missing one
  ([decision 0021](docs/decisions/0021-per-caller-access.md)).
- **Read-only by default.** Never widen `READ_ONLY` or default permissions. New capabilities must
  declare `Permission`s. Consequential permissions need per-call approval
  ([decision 0005](docs/decisions/0005-approval-gates.md)). Never create approvals from tool
  or model output, and never pre-approve a permission in code or defaults.
- **Programs run through `run_command`.** Never call `subprocess` from a tool; the runner
  scrubs the environment and kills the process tree on timeout.
- **Retrieved text never steers the platform.** No tool reads tool output or retrieved text to
  choose what runs next. Retrieved text stays marked untrusted, and anything synthesized from it
  is labelled `interpretation`.
- **Ingestion is an operator command.** Writes to the knowledge store are not agent tools. Nor are `qaos eval`, `qaos runs
  prune`, and `qaos runs export`.
- **Per-caller access fails closed.** Once a policy applies, anything uncertain is a refusal, and a refused dataset looks like a
  missing one ([decision 0021](docs/decisions/0021-per-caller-access.md)). A configuration an interface would ignore is
  refused or warned about, never silently dropped.
- **Declared permissions are checked, not sandboxed.** Never describe a tool's permissions as a limit on what its code can do
  ([finding F-6](docs/security-review.md#findings)).
- **Explicit registration.** No dynamic plugin discovery or code loading, and no tool
  marketplace. A firm's tools are named in its own server's code
  ([decision 0020](docs/decisions/0020-a-firms-own-server.md)). The Claude Code plugin manifest in `.claude-plugin/` only packages the server and
  the skills for clients; it adds no tools.
- **MCP is the capability boundary; skills are guidance.** Skills contain instructions only, never
  scripts, so every action goes through a tool and the executor's permissions, approvals,
  limits, and audit. `tests/server/test_skills.py` fails when a skill names
  something the server does not serve, or a served tool, prompt, or resource has no skill.
- **No server-side agents.** A workflow the platform owns is a deterministic composite tool,
  such as `research_report`. `Platform.report` runs calls its caller names.
- **Data by reference.** Tools take dataset ids and resource URIs; inline values are for small
  examples only, so numbers do not travel through the model.
- **Writes to the user's machine stay local.** Server tools compute, retrieve, and validate.
  Creating files, as `qaos app create` does, is a command the user runs.
- **Efficiency is measured.** A change to the tool surface or the skills reports its delta in
  the agent-efficiency evaluation; rebaseline only for an intended change.
- **Offline tests.** Tests run offline. Anything that needs a network is opt-in and never part of
  default CI.
- **Secrets.** Secrets are `SecretStr`, never logged or displayed, and `.env` is never committed.
- **Facts versus interpretation.** Reports keep `computed`, `retrieved`, and `interpretation`
  evidence distinct. Financial metrics need documented conventions and known-answer tests.
- **Say what was measured.** Describe Quant Research as it measures on real papers, not as "Graph
  RAG": keyword search does most of the ranking, and the graph is small
  ([results](docs/products/quant-research.md#how-well-it-works)). A quality claim cites a measurement.
- **Docs stay true.** `tests/contract/test_docs.py` checks relative links and the commands the README
  and getting started teach. Generated metrics stay in step with the platform's
  (`tests/factory/test_metric_parity.py`).

## Conventions

- Python 3.11+, strict mypy, ruff (line length 100), and docstrings on public functions.
- Raise specific error types with actionable messages. Executor failures are recorded on the
  `ToolCallRecord`, not raised.
- Add a `CHANGELOG.md` entry under "Unreleased" for user-visible changes.

## Boundaries

Ask before you:
- add dependencies or provider SDKs;
- change permissions or security defaults;
- edit CI or release workflows;
- bump versions, tag, or publish.

Names are provisional; do not register domains, packages, or trademarks.
