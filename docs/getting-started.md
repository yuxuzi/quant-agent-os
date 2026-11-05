# Getting started

Quant-Agent-OS reaches your AI client as one MCP server plus four skills that teach the client
how to use it. You need [uv](https://docs.astral.sh/uv/). From nothing to a first answer:

```bash
uv tool install "quant-agent-os[analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@v0.2.0"
qaos ingest ~/research            # or `qaos init --sample` to try it first
qaos data add returns.csv
qaos connect claude --write       # or cursor, vscode; restart the client
```

Replace `TAG` with a [release](https://github.com/yuxuzi/quant-agent-os/releases) that has `qaos`.
`qaos doctor` explains anything that is missing. Add extras as you need them: `pdf` for PDF
ingestion, `postgres` for a PostgreSQL store, `factory` for `qaos app`, or `all`.

## 1. Add the skills (Claude Code)

`qaos connect` gives the client the server. The skills teach it how to use the server, and in
Claude Code a plugin installs both, so it can replace `qaos connect`. In a terminal:

```bash
claude plugin marketplace add yuxuzi/quant-agent-os
claude plugin install quant-agent-os@quant-agent-os
```

Or inside a session (Claude Code 2.1.275 or later):

```text
/plugin install quant-agent-os --marketplace yuxuzi/quant-agent-os
```

The plugin brings:
- **The MCP server.** `qaos serve` serves research and analytics tools, with
  resources and prompts. It runs locally through `uv` on Python 3.13, and on first
  start uv installs its locked dependencies.
- **Four skills,** loaded when a task needs them:
  - `quant-research` for cited answers from your corpus;
  - `quant-analytics` for metrics by dataset reference;
  - `quant-strategy` for cited strategy specs and reports;
  - `quant-app-factory` for project specs you create locally, when the server is started with
    `--toolsets research,analytics,factory`.

The server is read-only by default and records every call in the home's `runs.db` (`qaos runs`).

## 2. Give it your data (operator commands)

Ingesting documents and adding datasets are commands you run, never tools the model can call.


```bash
qaos ingest ~/research
qaos data add returns.csv         # the id is the file's name, or use --id
```

- **One home.** Everything lives in `~/.quant-agent-os`, or in `QUANT_AGENT_OS_HOME`, and every
  command uses it with no flags ([configuration](configuration.md)).
- **The knowledge store.** For a store elsewhere, including a `postgresql://` URL, set
  `QUANT_AGENT_OS_STORE`. Without a store, the server still starts, without the research tools,
  and says so.
- **To try it first,** `qaos init --sample` loads a small corpus and a dataset.

## 3. Ask

- "What does my research say about short-term reversal? Cite the passages."
- "Report on trend following, with the metrics of the fund column in the returns dataset."
- "What drives the risk of my portfolio in the holdings dataset?"

Answers cite your documents by file and line. Metrics come from the server, with their
conventions, and are never computed by the model. To see exactly what the client is offered,
run `qaos catalog`.

## Other clients

- **Cursor and VS Code (Copilot).** `qaos connect cursor --write` or `qaos connect vscode
  --write` adds the server to the project. For the skills, copy the rules file from
  [clients/](../clients/README.md).
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
`uv run python tests/server/client_bundles.py`.

Next: play with the [tutorial](tutorial.md), then read [architecture.md](architecture.md) and
[configuration.md](configuration.md).
