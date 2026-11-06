# quant-agent-os

**Quant-Agent-OS** gives AI clients such as Claude, Copilot, and Cursor quantitative research tools
with cited, computed evidence. It is not a chatbot and calls no model: your client does the
conversation, and the tools reach it over MCP, REST, and a Python SDK. You bring your own documents
and data.

- **Quant Research:** cited keyword search with entity linking and a small graph of extracted facts,
  plus optional semantic search, over your own notes and papers.
  [What it measures on real papers](docs/products/quant-research.md#how-well-it-works).
- **Quant Analytics:** performance and risk metrics with stated conventions, computed on your
  datasets by reference, so numbers do not travel through the model.
- **Quant App Factory:** generates tested Python projects from a small spec, off by default on the
  server. It uses the standalone [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) scaffolder.

> Quant-Agent-OS is a working name that has not been checked for availability. This project is
> alpha research software (0.2.0), not investment advice.

## Start

[uv](https://docs.astral.sh/uv/) is the only prerequisite. From nothing to a first answer:

```bash
uv tool install --python 3.13 "quant-agent-os[analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@v0.2.0"
qaos ingest ~/research            # or `qaos init --sample` to try it first
qaos data add returns.csv
qaos connect claude --write       # or cursor, vscode; `qaos doctor` explains anything missing
```

Then ask your client: "What does my research say about short-term reversal? Cite the passages."

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
is read-only by default, writes nothing to the store, and sends nothing off the machine unless you
choose a hosted embedder. Each call is recorded (`qaos runs`).

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
src/quant_app_factory/   Quant App Factory and its template packs
src/quant_platform/      `qaos`, one server for every toolset, and the hosted command group
skills/                  instruction-only skills; clients/: Cursor and VS Code bundles
.claude-plugin/          Claude Code plugin and marketplace manifests
deploy/                  team-server Dockerfile, compose files, and smoke test
examples/  scripts/  tests/  docs/
```

## Documentation

- [Getting started](docs/getting-started.md), then the [tutorial](docs/tutorial.md)
- Products: [Quant Research](docs/products/quant-research.md),
  [Quant Analytics](docs/products/quant-analytics.md),
  [Quant App Factory](docs/products/quant-app-factory.md)
- [Configuration](docs/configuration.md), [security](docs/security.md), and a
  [team server in Docker](docs/deployment.md) with its [operations](docs/operations.md);
  [hosting many customers](docs/hosted.md)
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
