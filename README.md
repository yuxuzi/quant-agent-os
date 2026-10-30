# quant-agent-os

**Quant-Agent-OS** is an open-source quantitative intelligence and application-engineering
platform. It is not a chatbot: Claude, Copilot, Cursor, and other AI clients use its typed tools,
bounded workflows, and evidence-linked reports through MCP and a Python SDK. You bring your own
models, data, and documents.

It has two products built on one core platform:
- **Quant Research**: a Graph RAG knowledge system. It connects factors, signals, strategies,
  and papers, and every answer is backed by cited passages.
- **Quant App Factory**: it turns requirements into tested, packaged Python applications. It
  uses the standalone [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) scaffolder
  through a replaceable adapter.

> Quant-Agent-OS is a working name that has not been checked for availability. This project is
> pre-alpha research software, not investment advice.

## Status

| Area | State |
| --- | --- |
| Core: tool contract, registry, permissions, bounded executor, orchestrator, evidence reports | Implemented |
| Core: MCP adapter | Implemented; stdio server is next (backlog B-01) |
| Core: run provenance (run and call ids, input hashes, registry digest, data versions) | Implemented |
| Core: run store, approvals, SDK facade | Planned for the MVP |
| Quant Research (Graph RAG) | Implemented over SQLite: ingestion, keyword, mention, and graph retrieval, cited evidence packets, read-only tools, evaluation ([architecture §6–7](docs/architecture.md)) |
| Quant App Factory | Designed ([architecture §8–9](docs/architecture.md)); package skeleton only |
| Quant analytics | Example metrics only; separate package planned |

The roadmap, gap analysis, and backlog are in [docs/ENGINEERING_REVIEW_PLAN.md](docs/ENGINEERING_REVIEW_PLAN.md).

## Quick start

[uv](https://docs.astral.sh/uv/) is the only prerequisite.

```bash
uv sync
uv run python examples/portfolio-research/portfolio_research.py   # offline end-to-end demo
uv run python examples/quant-research/research_demo.py            # Graph RAG demo, offline
uv run quant-agent-os info                                        # active settings, secrets hidden
uv run pytest
```

## How it fits together

```text
request ─▶ Orchestrator ─▶ ModelProvider (BYO model) ─▶ Plan (validated: allowlist, step limit)
                │
                ▼
          ToolExecutor ─▶ registered tools (plain Python functions)
                │          permissions · input/output validation · timeout · output size
                ▼
          Report: findings (computed / retrieved / interpretation) + sources,
                  warnings, limitations, per-call provenance
```

MCP clients reach the same tools through `quant_agent_os.mcp`, which calls the same executor, so
every boundary gets identical validation and limits.

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
src/quant_agent_os/      Core platform (products will live in packages/, see decision 0003)
  tools/          contract (ToolSpec, @tool), registry, permissions
  runtime/        plan models, bounded executor, orchestrator
  providers/      ModelProvider protocol and FakeModelProvider (bring your own model)
  config/         settings from QUANT_AGENT_OS_* environment variables
  evidence/       Evidence and SourceRef (facts vs interpretation)
  reporting/      Report models and Markdown rendering
  mcp/            MCP tool definitions and calls over the registry
  observability/  logging with secret redaction
packages/quant-research/   Quant Research (Graph RAG) product
packages/quant-app-factory/  Quant App Factory product (skeleton)
apps/quant-ai-assistant/   superseded placeholder (see decision 0003)
examples/portfolio-research/  examples/quant-research/
tests/  unit/  contract/  integration/  evaluation/
docs/   architecture, configuration, security, getting started, decisions, roadmap
```

## Documentation

- [Getting started](docs/getting-started.md)
- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Security](docs/security.md)
- [Architecture decisions](docs/decisions/)
- [Contributing](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) for AI coding agents

## License

MIT. See [LICENSE](LICENSE).
