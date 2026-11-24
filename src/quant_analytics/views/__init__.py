"""Quant Analytics' views: the pages an MCP Apps client draws a tool's result in (decision 0036).

A view is one static HTML file shipped beside this module. It computes nothing and fetches
nothing: it draws the structured result of the tool it names, and a client without MCP Apps
shows that tool's text as before.
"""

from __future__ import annotations

from importlib.resources import files

from quant_agent_os.tools import ViewSpec

PERFORMANCE_VIEW = ViewSpec(
    uri="ui://quant-agent-os/performance.html",
    name="performance",
    description=(
        "Performance of a returns series, drawn as a page for clients that show MCP Apps "
        "(analyze_performance)."
    ),
    html=files("quant_analytics.views").joinpath("performance.html").read_text(encoding="utf-8"),
    tools=("analyze_performance",),
)
