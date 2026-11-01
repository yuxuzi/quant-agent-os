"""The portfolio-research example's tools, from the Quant Analytics package.

They used to be defined here; they now live in `quant_analytics.tools`, with the full
`analyze_performance` tool beside them. This module keeps the example's imports working.
"""

from __future__ import annotations

from quant_agent_os.tools import ToolRegistry
from quant_analytics.tools import (
    MetricOutput,
    ReturnsInput,
    analyze_performance,
    annualized_return,
    max_drawdown,
)

__all__ = [
    "MetricOutput",
    "ReturnsInput",
    "analyze_performance",
    "annualized_return",
    "build_registry",
    "max_drawdown",
]


def build_registry() -> ToolRegistry:
    """Return a registry containing the example tools."""
    return ToolRegistry([annualized_return, max_drawdown])
