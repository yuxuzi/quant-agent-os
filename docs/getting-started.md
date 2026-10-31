# Getting started

## Install

```bash
git clone https://github.com/yuxuzi/quant-agent-os
cd quant-agent-os
uv sync
```

`uv` installs a supported Python (3.11–3.15) and every development tool. No virtualenv activation
is needed.

## Run the offline example

```bash
uv run python examples/portfolio-research/portfolio_research.py
```

A fake provider returns a scripted plan, two deterministic tools compute metrics on synthetic
returns, and a Markdown report is printed. Edit
`examples/portfolio-research/sample_config.toml` to try other data or tools.

## Use it from Claude Code

```bash
uv run quant-research ingest packages/quant-research/tests/fixtures/corpus --store "$PWD/.kb.db"
claude mcp add quant-research -- uv run --directory "$PWD" quant-research serve-mcp --store "$PWD/.kb.db"
```

Then ask Claude Code something like "What is related to short-term reversal in my research
notes?". Remove the server with `claude mcp remove quant-research`.

## Use it from Python

```python
import json

from portfolio_tools import build_registry  # examples/portfolio-research
from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.reporting import render_markdown
from quant_agent_os.runtime.orchestrator import Orchestrator

plan = {
    "goal": "Drawdown check",
    "steps": [
        {"tool": "max_drawdown", "arguments": {"series_id": "demo", "returns": [0.1, -0.5, 0.2]}},
    ],
}
orchestrator = Orchestrator(build_registry(), FakeModelProvider([json.dumps(plan)]))
print(render_markdown(orchestrator.run("Drawdown check")))
```

## Develop

```bash
uv run pytest                    # unit, contract, and integration tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                      # strict
```

Next: read [architecture.md](architecture.md), then [configuration.md](configuration.md).
