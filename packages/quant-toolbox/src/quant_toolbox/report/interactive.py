"""Interactive charts: a `ChartBlock` drawn by plotly's graph objects (never plotly express).

plotly comes with the ``interactive`` extra; pandas is not needed. Importing this module never
fails; drawing without plotly raises `quant_toolbox.MissingExtraError`.
"""

from __future__ import annotations

import math
from typing import Any

from quant_toolbox._optional import require
from quant_toolbox.report._document import COLOURS, ChartBlock


def figure(block: ChartBlock) -> Any:
    """``block`` as a plotly ``Figure``, for a notebook or further styling."""
    graph = require("plotly.graph_objects", extra="interactive", purpose="interactive charts")
    traces = []
    for index, (name, values) in enumerate(block.series.items()):
        colour = COLOURS[index % len(COLOURS)]
        cleaned = [float(v) if v is not None and math.isfinite(v) else None for v in values]
        if block.kind == "bar":
            traces.append(graph.Bar(x=block.labels, y=cleaned, name=name, marker_color=colour))
        else:
            dates = [day.isoformat() for day in block.dates]
            traces.append(
                graph.Scatter(x=dates, y=cleaned, name=name, mode="lines", line_color=colour)
            )
    chart = graph.Figure(traces)
    chart.update_layout(
        title=block.title,
        template="plotly_white",
        height=360,
        margin={"l": 48, "r": 16, "t": 48, "b": 40},
        legend={"orientation": "h"},
        showlegend=len(block.series) > 1,
    )
    if block.percent:
        chart.update_yaxes(tickformat=".1%")
    return chart


def chart_html(block: ChartBlock, *, include_plotlyjs: bool | str = True) -> str:
    """``block`` as an HTML fragment; plotly.js inline (``True``), from its CDN (``"cdn"``), or
    not at all (``False``, when the page already has it)."""
    return str(figure(block).to_html(full_html=False, include_plotlyjs=include_plotlyjs))
