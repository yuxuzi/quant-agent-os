"""Tutorial lab: performance and risk metrics on datasets the tools read by id.

Usage: uv run python examples/tutorial/lab_analytics.py [DATASETS_DIR]

The datasets directory defaults to $QUANT_AGENT_OS_DATASETS_DIR. Push the two sample datasets
first, as docs/tutorial.md shows. The script passes dataset ids, never values, which is what an
AI client does through MCP.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.sdk import Platform
from quant_analytics.tools import analytics_tools

WEIGHTS = {"equities": 0.6, "bonds": 0.3, "gold": 0.1}


def ok(platform: Platform, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call ``tool`` and return its output, or exit with the recorded failure."""
    record = platform.call(tool, arguments)
    if record.output is None:
        raise SystemExit(f"{tool} {record.status}: {record.error}")
    return record.output


def main() -> None:
    """Print the fund's performance against its benchmark, then the portfolio's risk."""
    directory = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QUANT_AGENT_OS_DATASETS_DIR")
    if not directory:
        raise SystemExit("Give a datasets directory, or set QUANT_AGENT_OS_DATASETS_DIR.")
    with Platform(analytics_tools(DirectoryDatasetStore(Path(directory)))) as platform:
        performance = ok(
            platform,
            "analyze_performance",
            {
                "returns": {"dataset": "fund", "column": "fund"},
                "benchmark": {"dataset": "fund", "column": "benchmark"},
            },
        )
        print(f"Performance of {performance['series_id']} over {performance['periods']} months")
        for name in ("annualized_return", "sharpe_ratio", "max_drawdown", "beta", "alpha"):
            print(f"  {name:<18} {performance['metrics'][name]:>8.4f}")
        print(f"  Sharpe convention: {performance['conventions']['sharpe_ratio']}")
        print(f"  Data version: {performance['data_versions']['dataset:fund'][:16]}")
        print(f"  Warnings: {performance['warnings'] or 'none'}")

        risk = ok(
            platform,
            "analyze_risk",
            {
                "portfolio_id": "60-30-10",
                "weights": WEIGHTS,
                "asset_returns": {"dataset": "portfolio"},
                "factor_returns": {"dataset": "portfolio", "columns": ["market", "rates"]},
            },
        )
        print(f"\nRisk of {risk['portfolio_id']}: volatility {risk['volatility']:.2%} a year")
        for asset, item in risk["contributions"].items():
            print(
                f"  {asset:<9} weight {item['weight']:>4.0%}  "
                f"share of risk {item['percent_contribution']:>6.1%}"
            )
        factors = risk["factors"]
        print(f"  Factor exposures: { {k: round(v, 2) for k, v in factors['exposures'].items()} }")
        print(f"  The factors explain {factors['r_squared']:.1%} of the variance.")


if __name__ == "__main__":
    main()
