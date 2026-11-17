"""Reports: documents of tables, charts, and text; renderers; chart builders; run reports.

A section of a report returns blocks and knows nothing about formats. `render_html` draws charts
as static SVG with pygal by default, or interactively with plotly (``charts="interactive"``, the
``interactive`` extra). Optional leaves: `quant_toolbox.report.svg` (pygal),
`quant_toolbox.report.interactive` (plotly), and `quant_toolbox.report.excel` (openpyxl).
"""

from quant_toolbox.report._charts import (
    bar_chart,
    equity_curve,
    line_chart,
    rolling_sharpe_chart,
    underwater_chart,
)
from quant_toolbox.report._document import (
    CHART_MODES,
    COLOURS,
    Block,
    Cell,
    ChartBlock,
    Column,
    Document,
    TableBlock,
    TextBlock,
    format_cell,
    render_html,
    render_markdown,
    to_dict,
)
from quant_toolbox.report._format import METRIC_KINDS, format_metric, label, metric_kind
from quant_toolbox.report._runs import (
    INTERPRETATION,
    comparison_markdown,
    equity_chart,
    run_calendar_years,
    run_markdown,
)

__all__ = [
    "CHART_MODES",
    "COLOURS",
    "INTERPRETATION",
    "METRIC_KINDS",
    "Block",
    "Cell",
    "ChartBlock",
    "Column",
    "Document",
    "TableBlock",
    "TextBlock",
    "bar_chart",
    "comparison_markdown",
    "equity_chart",
    "equity_curve",
    "format_cell",
    "format_metric",
    "label",
    "line_chart",
    "metric_kind",
    "render_html",
    "render_markdown",
    "rolling_sharpe_chart",
    "run_calendar_years",
    "run_markdown",
    "to_dict",
    "underwater_chart",
]
