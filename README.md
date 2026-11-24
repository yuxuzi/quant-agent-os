# quant-agent-os

**Quant-Agent-OS** connects AI clients such as Claude, Copilot, and Cursor to a firm's own Python tools, research,
and data, on a server the firm runs. It decides who reaches what, bounds every call, and records it. It is not a chatbot
and calls no model: your client does the conversation, and the tools reach it over MCP, REST, and a Python SDK.

- **Your numbers, from your code.** [Serve your Python functions](docs/deployment.md#8-add-your-own-tools) beside the
  platform's performance, risk, signal, and backtest tools. Every figure states its convention and data version, and
  inputs pass by reference, so series do not travel through the model.
- **Your data, mapped once.** Read your warehouse in place over SQL, or files, and
  [map it to six standard tables](docs/deployment.md#map-your-data-to-the-standard-tables) (securities, prices,
  returns, holdings, measures, reference); a source too complicated for today gets a stub adapter to finish later.
- **Your research, cited.** Keyword search with entity linking and a small graph of extracted facts, plus optional
  semantic search, over your notes and papers; a search that finds nothing says so.
  [What it measures on real papers](docs/products/quant-research.md#how-well-it-works).
- **Applications you own.** Generate a tested application for one job, such as a performance report that
  reconciles with your administrator's figures, then let a coding assistant change it while its tests guard the
  numbers. [Your existing Python becomes tools](docs/products/quant-app-factory.md#legacy-code-as-tools) the same way.
- **Under your control.** [Per-caller access](docs/deployment.md#9-decide-who-reaches-what) through your identity
  provider, read-only by default, approvals for anything consequential, and
  [a record of every AI use](docs/operations.md#show-how-ai-was-used) your compliance staff can keep, with no content
  in it.
- **Current with your AI clients.** MCP 2026-07-28 and the earlier handshake from one server, so Claude, Copilot,
  Cursor, and others connect. Clients that show MCP Apps draw a performance result
  [as a page in the chat](docs/products/quant-analytics.md#see-the-result-in-your-client).
- **Stable from 1.0.** [What 1.0 promises](docs/decisions/0035-what-1.0-promises.md): tools, Python names, files,
  settings, commands, and routes follow semantic versioning, and checks hold them.

**What you operate.** You run the server, in Docker, with TLS and rate limits in your own proxy. You decide who holds a key,
keep the backups, and apply upgrades, and [operations](docs/operations.md) covers how. Results go to the client you connect,
and so to its model. [Who is responsible for what](docs/security.md#who-is-responsible-for-what) sets it out, with what this
does not protect against.

**See it working:** [getting started](docs/getting-started.md#see-it-all-together) ends with the Portfolio Intelligence
demonstration, which runs one fictional firm's portfolio research and risk analysis, end to end, with no model or network.

> Quant-Agent-OS is a working name that has not been checked for availability. This project is
> research software, not investment advice.

## Start

[uv](https://docs.astral.sh/uv/) is the only prerequisite. From nothing to a first answer:

```bash
uv tool install --python 3.13 "quant-agent-os[analytics,server,factory] @ git+https://github.com/yuxuzi/quant-agent-os@v1.0.0"
qaos ingest ~/research            # or `qaos init --sample` to try it first
qaos data add returns.csv
qaos connect claude --write       # or cursor, vscode; `qaos doctor` explains anything missing
```

Then ask your client: "What does my research say about short-term reversal? Cite the passages."

Or start from an application that already works: `qaos app new performance-report my-report --defaults` generates a
tested project that turns a returns file into horizon tables and reconciles them with your administrator's figures.

**Read next:** [getting started](docs/getting-started.md) covers the skills, your own data, other
clients, and notebooks. After that, the [tutorial](docs/tutorial.md) is a set of short labs to play
with.

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

The platform has no planner ([decision 0016](docs/decisions/0016-clients-orchestrate.md)). Every
interface reaches the same executor, so every caller gets identical validation and limits. The server
is read-only by default and writes nothing to the store. Beyond what it returns to your client, it sends nothing off the
machine unless you choose a hosted embedder. Each call is recorded (`qaos runs`).

## Also included

**Quant App Factory** generates tested Python projects from a small spec, including one you can change for each stage of a
fund's work: alpha research, strategy backtest, risk monitor, and performance report. It is optional, and off by default on
the server. It uses the standalone [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) scaffolder.

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
src/quant_agent_os/      core: tool contract, executor, evidence, runs, MCP, REST, SDK
src/quant_research/      Quant Research
src/quant_analytics/     Quant Analytics
packages/quant-toolbox/ Quant Toolbox: the MIT library under analytics and generated apps
src/quant_app_factory/   Quant App Factory and its template packs
src/quant_platform/      `qaos`, one server for every toolset, sign-in, and approvals
skills/                  instruction-only skills; clients/: Cursor and VS Code bundles
.claude-plugin/          Claude Code plugin and marketplace manifests
deploy/                  team-server Dockerfile, compose files, smoke test, and upgrade drill
examples/  scripts/  tests/  docs/
```

## Documentation

- [Getting started](docs/getting-started.md), then the [tutorial](docs/tutorial.md)
- Products: [Quant Research](docs/products/quant-research.md),
  [Quant Analytics](docs/products/quant-analytics.md),
  [Quant App Factory](docs/products/quant-app-factory.md), and the library under them,
  [Quant Toolbox](docs/products/quant-toolbox.md)
- [Configuration](docs/configuration.md), [security](docs/security.md), and a
  [team server in Docker](docs/deployment.md) with its [operations](docs/operations.md)
- [Architecture](docs/architecture.md), [decisions](docs/decisions/), and
  [open backlog](docs/ENGINEERING_REVIEW_PLAN.md)
- [Tools changelog](docs/tools-changelog.md) and [releasing](docs/releasing.md)
- [Contributing](CONTRIBUTING.md), and [AGENTS.md](AGENTS.md) for AI coding agents

## License

Source-available under the [Business Source License 1.1](LICENSE): you may read, modify, and evaluate it
freely, and production use needs a commercial license. Each version becomes Apache 2.0 four years after its
release. `skills/`, `clients/`, and the App Factory's template packs are MIT, so generated projects carry no
platform license. Versions up to v0.2.0, and `main` before this license was added, stay MIT
([decision 0019](docs/decisions/0019-business-source-license.md)).
