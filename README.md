# quant-agent-os

**Quant-Agent-OS** is an open-source quantitative intelligence and application-engineering
platform. It is not a chatbot: Claude, Copilot, Cursor, and other AI clients use its typed tools
and evidence-linked reports through MCP and a Python SDK. You bring your own
models, data, and documents.

It has two products built on one core platform:
- **Quant Research**: a Graph RAG knowledge system. It connects factors, signals, strategies,
  and papers, and every answer is backed by cited passages.
- **Quant App Factory**: it turns requirements into tested, packaged Python applications. It
  uses the standalone [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) scaffolder
  through a replaceable adapter.

> Quant-Agent-OS is a working name that has not been checked for availability. This project is
> alpha research software (0.1.2; 0.1.0 was the first MVP release), not investment advice.

## Status

| Area | State |
| --- | --- |
| Core: tool contract, registry, permissions, bounded executor, evidence reports | Implemented |
| Core: MCP adapter and stdio server | Implemented (`quant-agent-os[mcp]`); `quant-research serve-mcp` serves the research tools |
| Core: REST interface with scoped API keys | Implemented (`quant-agent-os[api]`, [decision 0011](docs/decisions/0011-rest-api-and-api-keys.md)); `quant-research serve-api`, `quant-agent-os api-key new` |
| Remote MCP, tenants, and OAuth | Implemented: `quant-platform serve-http` serves MCP over Streamable HTTP and REST from one app with API keys; `quant-platform hosted` adds a catalog per tenant and OAuth 2.1 tokens ([decisions 0012](docs/decisions/0012-remote-mcp-over-http.md), [0013](docs/decisions/0013-identity-and-tenancy.md)) |
| Approvals in the client | Implemented: a consequential call asks the user in the client (stdio) or sends them to an approval page (HTTP) ([decision 0014](docs/decisions/0014-approvals-in-the-client.md)) |
| Metering, plans, and quotas | Implemented: a usage event per call, `quant-platform hosted usage export`, plans with toolsets, rate limits, and monthly quotas per tenant ([decision 0015](docs/decisions/0015-metering-quotas-and-entitlements.md)) |
| Tool compatibility | Implemented: semver tool versions checked against `tool-versions.lock`, deprecations, `qaos://server/info` ([tools changelog](docs/tools-changelog.md)) |
| Team server in Docker | Implemented on SQLite or PostgreSQL + pgvector ([docs/deployment.md](docs/deployment.md)); both smoke-tested by `docker compose up` in CI |
| Skills and client packaging | Implemented: four instruction-only skills kept in step with the server by tests, a Claude Code plugin, and Cursor and VS Code bundles ([getting started](docs/getting-started.md)) |
| Core: run provenance (run and call ids, input hashes, registry digest, data versions) | Implemented |
| Core: run store with append-only audit, SDK `Platform` facade | Implemented; `serve-mcp` records every call (`quant-research runs`) |
| Core: approvals for consequential permissions, subprocess runner | Implemented ([decision 0005](docs/decisions/0005-approval-gates.md)): single-use approvals per call, standing approvals opt-in |
| Quant Research (Graph RAG) | Implemented over SQLite or PostgreSQL with pgvector (same answers; `quant-agent-os-research[postgres]`): ingestion, keyword, mention, and graph retrieval, cited evidence packets, read-only tools, evaluation on fixtures and [21 real papers](docs/plans/real-corpus-eval.md) ([architecture §6–7](docs/architecture.md)) |
| Quant App Factory | Foundation implemented: `quant-factory plan/create`, `create_app` for the terminal, an `agent-tool` template pack, pytemplate-uv 3.0 as the scaffolder; model-drafted specs and code generation are planned |
| Quant Analytics | Implemented: `analyze_performance` on empyrical-reloaded (Sharpe, Sortino, Calmar, drawdown, VaR, alpha/beta, tracking error, IR) and `analyze_risk` (volatility contributions, factor exposures, variance shares), with conventions pinned by known-answer tests ([decision 0010](docs/decisions/0010-analytics-on-empyrical.md)) |

The roadmap, gap analysis, and backlog are in [docs/ENGINEERING_REVIEW_PLAN.md](docs/ENGINEERING_REVIEW_PLAN.md).

## Quick start

[uv](https://docs.astral.sh/uv/) is the only prerequisite.

```bash
uv sync --locked
uv run python examples/portfolio-research/portfolio_research.py   # offline end-to-end demo
uv run python examples/quant-research/research_demo.py            # Graph RAG demo, offline
uv run python examples/quant-research/sdk_demo.py                 # SDK call and its recorded run
uv run quant-agent-os info                                        # active settings, secrets hidden
uv run pytest
```

New here? The [tutorial](docs/tutorial.md) is a set of short labs to play with, and it asks for
your feedback at the end.

## Use it from Claude

The quickest way is the Claude Code plugin. It installs the platform MCP server and the four
skills that teach Claude how to use it ([getting started](docs/getting-started.md)):

```bash
claude plugin marketplace add yuxuzi/quant-agent-os
claude plugin install quant-agent-os@quant-agent-os
```

Inside a session (Claude Code 2.1.275 or later):
`/plugin install quant-agent-os --marketplace yuxuzi/quant-agent-os`. For Cursor and VS Code
(Copilot), copy a bundle from [clients/](clients/README.md).

The [walkthrough](docs/walkthrough.md) goes from a clean checkout to cited answers in Claude
Code or Claude Desktop, with the recorded runs and known limitations. In short: ingest your
notes and papers once, then register the read-only research server with your MCP
client. Use absolute paths, because the client starts the server from its own directory.

```bash
uv run quant-research ingest ~/research --store ~/research/.kb.db
claude mcp add quant-research -- \
  uv run --directory /path/to/quant-agent-os quant-research serve-mcp --store ~/research/.kb.db
```

Claude Desktop uses the same command in `claude_desktop_config.json`. Give the full path to
`uv` (`which uv`), because Desktop does not read your shell's `PATH`:

```json
{"mcpServers": {"quant-research": {"command": "/Users/you/.local/bin/uv", "args": ["run",
  "--directory", "/path/to/quant-agent-os", "quant-research", "serve-mcp", "--store",
  "/Users/you/research/.kb.db"]}}}
```

Ask a research question, and Claude calls `find_related_signals` and `get_source_passage` and
cites file, page, and line ranges. Ask for a strategy, and `generate_strategy` returns a spec
whose every item cites its passages, with open questions for what the corpus leaves out. The
server is read-only, writes nothing to the store, and sends nothing off the machine unless you
choose a hosted `--embedder`. Each call is recorded
in `runs.db` beside the store (`quant-research runs`). A hosted embedder also
needs the `network` permission (see [docs/security.md](docs/security.md)).

Web apps and other languages can call the same tools over REST with an API key (install the
`api` extra):

```bash
KEY=$(uv run quant-agent-os api-key new --principal me --scope tools:list --scope tools:call)
uv run quant-research serve-api --store ~/research/.kb.db &      # http://127.0.0.1:8000
curl -H "Authorization: Bearer $KEY" -X POST http://127.0.0.1:8000/v1/tools/find_related_signals/call \
  -H 'content-type: application/json' -d '{"arguments": {"query": "short-term reversal"}}'
```

## Generate an app

One command starts a project with every default. Your confirmation is the recorded approval,
and the generated project's own checks run before it is reported done:

```bash
uv run quant-factory new quant-research my-lab --defaults   # asks once, then creates and checks
```

Or describe the app in a small TOML spec, preview it, then create it:

```bash
cat > signal-tools.toml <<'TOML'
name = "signal-tools"
template = "agent-tool"      # or quant-baseline, bundled (python, fastapi), or a pack path
profile = "agent-tool"
description = "Signal tools for Quant-Agent-OS"
output_dir = "."
tool_name = "count_signals"
TOML
uv run quant-factory plan signal-tools.toml     # what it builds; --verbose lists every file
uv run quant-factory create signal-tools.toml   # asks, creates, runs uv sync/ruff/mypy/pytest
```

An `agent-tool` package comes with one typed example tool, a registry, and tests that run the
platform's contract checks. A `quant-research` project holds a signal study, a backtest, and
a report in one place, with bias tests, and can load prices from a database. `uv run quant-factory new` asks the questions
for you. See [docs/walkthrough.md](docs/walkthrough.md#10-generate-a-tool-package) and
[§11](docs/walkthrough.md#11-a-quant-project-from-your-own-data-with-guidance).

## How it fits together

```text
AI client or your code ─▶ MCP · REST · SDK ─▶ ToolExecutor ─▶ registered tools (plain functions)
(plans, picks the tools)                       permissions · input and output validation
                                               approvals · timeout · output size
                                                    │
                                                    ▼
                               Evidence (computed / retrieved / interpretation) + sources,
                               warnings, limitations, per-call provenance, and an audit trail
```

The platform has no planner and calls no model ([decision 0016](docs/decisions/0016-clients-orchestrate.md)):
your client does the planning, and every interface reaches the same executor, so every caller
gets identical validation and limits. To drive the tools from your own model, see
[examples/your-own-model](examples/your-own-model/drive_tools.py).

## Define a tool

```python
from pydantic import BaseModel
from quant_agent_os.tools import ToolRegistry, tool


class Returns(BaseModel):
    returns: list[float]


class Metric(BaseModel):
    value: float
    warnings: list[str] = []


@tool(name="total_return", description="Compounded total return of simple returns.")
def total_return(params: Returns) -> Metric:
    growth = 1.0
    for r in params.returns:
        growth *= 1 + r
    return Metric(value=growth - 1)


registry = ToolRegistry([total_return])
```

## Repository layout

```text
src/quant_agent_os/      Core platform
  tools/          contract (ToolSpec, @tool), registry, permissions
  runtime/        tool calls, bounded executor, approvals, the subprocess runner
  config/         settings from QUANT_AGENT_OS_* environment variables
  evidence/       Evidence and SourceRef (facts vs interpretation)
  reporting/      Report models and Markdown rendering
  mcp/            MCP tools, resources, and prompts over the registry
  data/           datasets tools read by id, and list_datasets
  api/            REST routes over the executor and scoped API keys (optional api extra)
  runs/           run store (memory, SQLite), recorder, append-only audit, redaction
  sdk/            Platform facade: call, report, get_run
  observability/  logging with secret redaction
src/quant_research/      Quant Research (Graph RAG) product
src/quant_app_factory/   Quant App Factory product (spec, planner, pytemplate-uv adapter, agent-tool pack)
src/quant_analytics/     performance and risk metrics as tools
deploy/   team-server Dockerfile, compose file, and smoke test
src/quant_platform/      platform server: every toolset, resource, and prompt behind one MCP server
skills/   instruction-only skills (research, analytics, strategy, App Factory)
.claude-plugin/   Claude Code plugin and marketplace manifests; clients/: Cursor and VS Code bundles
examples/portfolio-research/  examples/quant-research/  examples/your-own-model/  examples/tutorial/
tests/  unit/  contract/  integration/  evaluation/
docs/   architecture, configuration, security, getting started, decisions, roadmap
```

## Documentation

- [Tutorial: hands-on labs with examples and flow charts](docs/tutorial.md)
- [Walkthrough: cited answers in Claude](docs/walkthrough.md)
- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security](docs/security.md)
- [Team server in Docker](docs/deployment.md), and [hosting many customers](docs/hosted.md)
- [Architecture decisions](docs/decisions/)
- [Tools changelog](docs/tools-changelog.md) and [Releasing](docs/releasing.md)
- [Contributing](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) for AI coding agents

## License

MIT. See [LICENSE](LICENSE).
