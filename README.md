# quant-agent-os

**QuantAgentOS** is the agent platform of the QuantFoundry ecosystem: typed tools, one bounded
orchestrator, and evidence-linked reports for quantitative research. It is open source and you
bring your own models and data.

> QuantFoundry, QuantAgentOS, and Quant AI Assistant are working names that have not been checked
> for availability. This project is pre-alpha research software, not investment advice.

## Status

| Milestone | State |
| --- | --- |
| Q0 Architecture and boundaries | [docs/architecture.md](docs/architecture.md), [decision records](docs/decisions/) |
| Q2 Tool contract and registry | Implemented: `quant_agent_os.tools` |
| Q3 Bring-your-own configuration | Settings and a provider interface with a fake provider; no real provider adapter yet |
| Q5 MCP boundary | Adapter implemented (`quant_agent_os.mcp`); server transport pending |
| Q6 Bounded orchestrator and report | Implemented with the fake provider and an offline example |
| Q1 analytics, Q4 research retrieval | Separate packages; not started here |

The full roadmap is in [docs/ENGINEERING_REVIEW_PLAN.md](docs/ENGINEERING_REVIEW_PLAN.md).

## Quick start

[uv](https://docs.astral.sh/uv/) is the only prerequisite.

```bash
uv sync
uv run python examples/portfolio-research/portfolio_research.py   # offline end-to-end demo
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
src/quant_agent_os/
  tools/          contract (ToolSpec, @tool), registry, permissions
  runtime/        plan models, bounded executor, orchestrator
  providers/      ModelProvider protocol and FakeModelProvider (bring your own model)
  config/         settings from QUANT_AGENT_OS_* environment variables
  evidence/       Evidence and SourceRef (facts vs interpretation)
  reporting/      Report models and Markdown rendering
  mcp/            MCP tool definitions and calls over the registry
  observability/  logging with secret redaction
apps/quant-ai-assistant/   flagship application (planned)
examples/portfolio-research/
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
