# AGENTS.md

Instructions and context for AI coding agents working on **quant-agent-os** (Quant-Agent-OS).
Claude Code reads this file through `CLAUDE.md`.

## What this project is

Quant-Agent-OS is an open-source quantitative intelligence and application-engineering
platform consumed by AI clients through MCP and a Python SDK. It is not a chatbot.
- **Core** (`src/quant_agent_os`): typed tool contract and registry, permission policy, bounded
  executor, one orchestrator, evidence-linked reports, and the MCP adapter.
- **Quant Research** (`packages/quant-research`, planned): a Graph RAG knowledge system.
- **Quant App Factory** (`packages/quant-app-factory`, planned): Python application
  generation.

Core must never import a product, and the products must not import each other. Quantitative
analytics live in a separate package and are registered as tools. `pytemplate-uv` is an
external tool: invoke it only through the App Factory's `ScaffoldProvider` adapter
(`uvx pytemplate-uv==3.0.*`). Never import, vendor, or plan changes to it here.

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
| Offline demo | `uv run python examples/portfolio-research/portfolio_research.py` |

Before reporting work as done, run the test, lint, format, and type-check commands and report
the real results.

## Layout and layering

`src/quant_agent_os/`, importing downward only:
1. `tools/`: contract (`ToolSpec`, `@tool`), registry, permissions.
2. `runtime/plan.py`, `runtime/executor.py`: plan models and bounded execution.
3. `evidence/`, `reporting/`: evidence, report models, rendering.
4. `runtime/orchestrator.py`: planning, validation, execution, report. Not re-exported from
   `runtime/__init__` to avoid an import cycle.
5. `providers/`, `config/`, `mcp/`, `observability/`, `cli.py`.

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
  ([decision 0005](docs/decisions/0005-approval-gates.md), proposed).
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
