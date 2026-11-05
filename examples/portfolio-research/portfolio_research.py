"""Run the portfolio-research example offline: named calls, deterministic tools, a Markdown report.

Usage: uv run python examples/portfolio-research/portfolio_research.py [CONFIG]
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

from portfolio_tools import build_registry

from quant_agent_os.reporting import Report, render_markdown
from quant_agent_os.runtime import ToolCall
from quant_agent_os.sdk import Platform

DEFAULT_CONFIG = Path(__file__).with_name("sample_config.toml")


def load_config(path: Path) -> dict[str, Any]:
    """Read the example configuration."""
    with path.open("rb") as handle:
        return tomllib.load(handle)


def run(config: dict[str, Any]) -> Report:
    """Compute the configured metrics as one recorded run, and return its report."""
    request = config["request"]
    series = config["series"]
    calls = [
        ToolCall(
            tool=name,
            arguments={
                "series_id": series["id"],
                "returns": series["returns"],
                "periods_per_year": series["periods_per_year"],
            },
        )
        for name in request["metrics"]
    ]
    with Platform(build_registry()) as platform:
        return platform.report(request["goal"], calls)


def main() -> None:
    """Run the example and print the report."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    print(render_markdown(run(load_config(path))))


if __name__ == "__main__":
    main()
