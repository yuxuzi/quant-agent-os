"""Run the portfolio-research example offline: scripted plan, deterministic tools, Markdown report.

Usage: uv run python examples/portfolio-research/portfolio_research.py [CONFIG]
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from portfolio_tools import build_registry

from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.reporting import Report, render_markdown
from quant_agent_os.runtime.orchestrator import Orchestrator

DEFAULT_CONFIG = Path(__file__).with_name("sample_config.toml")


def load_config(path: Path) -> dict[str, Any]:
    """Read the example configuration."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def run(config: dict[str, Any]) -> Report:
    """Run the configured request with a fake provider that returns the scripted plan."""
    request = config["request"]
    series = config["series"]
    steps = [
        {
            "tool": name,
            "arguments": {
                "series_id": series["id"],
                "returns": series["returns"],
                "periods_per_year": series["periods_per_year"],
            },
            "purpose": f"Compute {name.replace('_', ' ')}",
        }
        for name in request["metrics"]
    ]
    provider = FakeModelProvider([json.dumps({"goal": request["goal"], "steps": steps})])
    orchestrator = Orchestrator(build_registry(), provider, max_steps=config["limits"]["max_steps"])
    return orchestrator.run(request["goal"])


def main() -> None:
    """Run the example and print the report."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    print(render_markdown(run(load_config(path))))


if __name__ == "__main__":
    main()
