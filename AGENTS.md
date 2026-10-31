# AGENTS.md

Instructions and context for AI coding agents working on **quant-agent-os** (Quant-Agent-OS).
Claude Code reads this file through `CLAUDE.md`.

## What this project is

Quant-Agent-OS is an open-source quantitative intelligence and application-engineering
platform consumed by AI clients through MCP and a Python SDK. It is not a chatbot.
- **Core** (`src/quant_agent_os`): typed tool contract and registry, permission policy, bounded
  executor, one orchestrator, evidence-linked reports, and the MCP adapter.
- **Quant Research** (`packages/quant-research`): a Graph RAG knowledge system. It is layered
  as `domain` → `application` → `infrastructure`, with `tools.py` and `cli.py` as interfaces and
  `bootstrap.py` as the composition root.
- **Quant App Factory** (`packages/quant-app-factory`): Python application generation. An
  `AppSpec` goes through the `ArchitecturePlanner` and a `ScaffoldProvider` (pytemplate-uv),
  and the generated project's checks run through the subprocess runner. Its `agent-tool`
  template pack is in `quant_app_factory/packs/`.

Core must never import a product, and the products must not import each other. Quantitative
analytics live in a separate package and are registered as tools. `pytemplate-uv` is an
third-party library of App Factory ([decision 0006](docs/decisions/0006-pytemplate-uv-as-a-library.md)):
- Import only its public `pytemplate.api`, and only in
  `quant_app_factory/infrastructure/pytemplate_uv.py`, behind the `ScaffoldProvider` port.
- Core and Quant Research never import it.
- Never vendor it, and never plan changes to it here.

Read [docs/architecture.md](docs/architecture.md) and the plan in
[docs/ENGINEERING_REVIEW_PLAN.md](docs/ENGINEERING_REVIEW_PLAN.md) before larger changes.

`uv` and `uvx` are the only required tools; never use pip, virtualenv activation, or make.

## Commands

| Task | Command |
| --- | --- |
| Install | `uv sync` |
| Test (unit, contract, integration) | `uv run pytest` |
| Lint | `uv run ruff check .` |
| Format check | `uv run ruff format --check .` |
| Type-check (strict, `src` and `examples`) | `uv run mypy` |
| Offline demos | `uv run python examples/portfolio-research/portfolio_research.py`, `uv run python examples/quant-research/research_demo.py`, `uv run python examples/quant-research/sdk_demo.py` |
| Research evaluation | `uv run python packages/quant-research/tests/research_eval.py` (add `--update` to rebaseline) |
| App Factory smoke test (opt-in; needs network) | `QUANT_FACTORY_SMOKE=1 uv run pytest packages/quant-app-factory/tests/test_factory_smoke.py` |
| Real-paper evaluation (opt-in; `fetch` needs network once) | `uv run python packages/quant-research/tests/real_corpus.py fetch`, then `... real_corpus.py eval` |

Before reporting work as done, run the test, lint, format, and type-check commands and report
the real results.

## Layout and layering

`src/quant_agent_os/`, importing downward only:
1. `tools/`: contract (`ToolSpec`, `@tool`), registry, permissions.
2. `runtime/plan.py`, `runtime/approvals.py`, `runtime/runner.py`, `runtime/executor.py`: plan
   models, approvals, the subprocess runner, and bounded execution.
3. `evidence/`, `reporting/`, `runs/`: evidence, report models, rendering; the run store,
   `RunRecorder`, and audit. `runs/` reuses only the `REDACTED` marker from
   `observability/logging.py`.
4. `runtime/orchestrator.py`: planning, validation, execution, report, and optional
   recording. Not re-exported from `runtime/__init__` to avoid an import cycle.
5. `providers/`, `config/`, `mcp/`, `sdk/`, `testing.py` (`check_tool`), `observability/`,
   `cli.py`.

Tests live in `tests/unit`, `tests/contract` (every tool honours the contract; MCP equals a
direct call), `tests/integration` (end-to-end with `FakeModelProvider`), and `tests/evaluation`
(opt-in quality evaluations).

## Invariants

- **One bounded orchestrator.** No multi-agent features without an evaluation showing value
  ([decision 0001](docs/decisions/0001-one-orchestrator-first.md)).
- **Business logic stays out of MCP.** MCP code only adapts the registry
  ([decision 0002](docs/decisions/0002-mcp-boundary.md)).
- **Read-only by default.** Never widen `READ_ONLY` or default permissions. New capabilities must
  declare `Permission`s. Consequential permissions need per-call approval
  ([decision 0005](docs/decisions/0005-approval-gates.md)). Never create approvals from tool
  or model output, and never pre-approve a permission in code or defaults.
- **Programs run through `run_command`.** Never call `subprocess` from a tool; the runner
  scrubs the environment and kills the process tree on timeout.
- **No feedback into planning.** Never feed tool output or retrieved text back into planning
  prompts. Synthesis from retrieved text is labelled `interpretation` and cannot choose tools.
- **Ingestion is an operator command.** Writes to the knowledge store are not agent tools.
- **Explicit registration.** No dynamic plugin discovery or code loading, and no marketplace.
- **Offline tests.** Tests run offline with `FakeModelProvider`. Live-model evaluations are opt-in
  and never part of default CI.
- **Secrets.** Secrets are `SecretStr`, never logged or displayed, and `.env` is never committed.
- **Facts versus interpretation.** Reports keep `computed`, `retrieved`, and `interpretation`
  evidence distinct. Financial metrics need documented conventions and known-answer tests.

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
