# Getting started

Quant-Agent-OS reaches your AI client as one MCP server plus four skills that teach the client
how to use it. You need [uv](https://docs.astral.sh/uv/). From nothing to a first answer:

```bash
uv tool install --python 3.13 "quant-agent-os[analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@v0.2.0"
qaos ingest ~/research            # or `qaos init --sample` to try it first
qaos data add returns.csv
qaos connect claude --write       # or cursor, vscode; restart the client
```

`--python 3.13` matters: on Python 3.15, the `analytics` extra's `bottleneck` dependency has no
prebuilt wheel, so it needs a working C compiler to install. 3.11 to 3.14 work too.

Use a newer [release](https://github.com/yuxuzi/quant-agent-os/releases) if there is one.
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

## Use it from a notebook

Install into the notebook's own environment, not with `uv tool install`, because a kernel cannot
import a tool's environment. The `analytics` extra brings pandas. `open_platform` is new since
0.2.0, so until the next release, install from `main`:

```bash
uv add "quant-agent-os[analytics] @ git+https://github.com/yuxuzi/quant-agent-os@main"
```

```python
from quant_agent_os.sdk import ToolCallError
from quant_platform import open_platform

platform = open_platform()  # the home's knowledge store and datasets, and its run log
platform.data.put("fund", returns)  # `returns` is a DataFrame with one column per series

series = {"dataset": "fund", "column": "fund"}
output = platform.call("analyze_performance", returns=series).result()
output["metrics"]["sharpe_ratio"], output["periods_per_year"], output["warnings"]

platform.call("find_related_signals", query="short-term reversal").result()["passages"]

try:
    platform.call("analyze_performance", returns={"dataset": "absent", "column": "fund"}).result()
except ToolCallError as failure:
    failure.status, failure.hint
```

A frame gets the same checks as a CSV file: the index gives the dates and the frequency, series
are joined on it, and missing values are left out with a warning. Sort dates oldest first, and keep
one column per series. `put` keeps a copy for this session and writes nothing to the home; to keep a
dataset, use `qaos data add`. A run records the frame's fingerprint, but not the frame.

`result()` returns the output or raises `ToolCallError`, while `record.status` and `record.error`
hold the same failure if you prefer to check. Every call shows in `qaos runs`, with the same
permissions as `qaos serve`. If the home has no knowledge store, `open_platform` warns, and the
analytics tools still work.

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
