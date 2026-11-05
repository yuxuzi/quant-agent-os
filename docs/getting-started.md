# Getting started

Quant-Agent-OS reaches your AI client as one MCP server plus four skills that teach the client
how to use it. In Claude Code, one plugin installs both.

## 1. Install the plugin (Claude Code)

You need [uv](https://docs.astral.sh/uv/) and Claude Code. In a terminal:

```bash
claude plugin marketplace add yuxuzi/quant-agent-os
claude plugin install quant-agent-os@quant-agent-os
```

Or inside a session (Claude Code 2.1.275 or later):

```text
/plugin install quant-agent-os --marketplace yuxuzi/quant-agent-os
```

The plugin brings:
- **The MCP server.** `quant-platform serve-mcp` serves research, analytics, and App Factory
  tools, with resources and prompts. It runs locally through `uv` on Python 3.13, and on first
  start uv installs its locked dependencies.
- **Four skills,** loaded when a task needs them:
  - `quant-research` for cited answers from your corpus;
  - `quant-analytics` for metrics by dataset reference;
  - `quant-strategy` for cited strategy specs and reports;
  - `quant-app-factory` for project specs you create locally.

The server is read-only by default and records every call in `~/.quant-agent-os/runs.db`.

## 2. Give it your data (operator commands)

Ingesting documents and adding datasets are commands you run, never tools the model can call.
They need a clone of the repository:

```bash
git clone https://github.com/yuxuzi/quant-agent-os && cd quant-agent-os && uv sync
uv run quant-research ingest ~/research --store ~/.quant-agent-os/knowledge.db
uv run quant-agent-os dataset push returns.csv --id returns
```

- **The knowledge store.** The server finds `~/.quant-agent-os/knowledge.db` by itself. For a
  store elsewhere, including a `postgresql://` URL, set `QUANT_AGENT_OS_STORE`. Without a
  store, the server still starts, without the research tools, and says so.
- **Datasets** go to `~/.quant-agent-os/datasets`, or to `QUANT_AGENT_OS_DATASETS_DIR`.
- **To try it first,** ingest the sample corpus in
  `packages/quant-research/tests/fixtures/corpus`.

## 3. Ask

- "What does my research say about short-term reversal? Cite the passages."
- "Report on trend following, with the metrics of the fund column in the returns dataset."
- "What drives the risk of my portfolio in the holdings dataset?"
- "Help me draft a quant research project spec."

Answers cite your documents by file and line. Metrics come from the server, with their
conventions, and are never computed by the model. To see exactly what the client is offered,
run `uv run quant-platform catalog`.

## Other clients

- **Cursor and VS Code (Copilot).** Copy a bundle from [clients/](../clients/README.md). It has
  the same server command, with the skills as a rules file.
- **Claude Code without the plugin.** Run from the clone:

  ```bash
  claude mcp add quant-agent-os -- uv run --directory "$PWD" --package quant-agent-os-platform --extra factory quant-platform serve-mcp
  ```

- **A team server over REST,** in Docker, with API keys: see [deployment.md](deployment.md).

## Use it from Python

The SDK calls the same tools through the same executor. `Platform.report` runs named calls as one
recorded run, and two deterministic tools compute metrics on synthetic returns, with no model:

```bash
uv run python examples/portfolio-research/portfolio_research.py
```

```python
from portfolio_tools import build_registry  # examples/portfolio-research
from quant_agent_os.reporting import render_markdown
from quant_agent_os.runtime import ToolCall
from quant_agent_os.sdk import Platform

drawdown = ToolCall(
    tool="max_drawdown", arguments={"series_id": "demo", "returns": [0.1, -0.5, 0.2]}
)
with Platform(build_registry()) as platform:
    print(render_markdown(platform.report("Drawdown check", [drawdown])))
```

To drive the tools from your own model, use your vendor's tool loop over `tool_definitions` and
`Platform.call`, as [examples/your-own-model](../examples/your-own-model/drive_tools.py) shows.

## Develop

```bash
uv sync
uv run pytest                    # unit, contract, and integration tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict
```

`uv` installs a supported Python (3.11 to 3.15) and every development tool; no virtualenv
activation is needed. After changing a skill, regenerate the client bundles with
`uv run python apps/quant-platform/tests/client_bundles.py`.

Next: play with the [tutorial](tutorial.md), then read [architecture.md](architecture.md) and
[configuration.md](configuration.md).
