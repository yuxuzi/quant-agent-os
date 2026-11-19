"""Reports: documents of tables, charts, and text; renderers; chart builders; run reports.

A section of a report returns blocks and knows nothing about formats. `render_html` draws charts
as static SVG with pygal by default, or interactively with plotly (``charts="interactive"``, the
``interactive`` extra). Optional leaves: `quant_toolbox.report.svg` (pygal),
`quant_toolbox.report.interactive` (plotly), `quant_toolbox.report.excel` (openpyxl), and
`quant_toolbox.report.pdf` (fpdf2, the ``pdf`` extra: `render_pdf` and `write_pdf`).

`tear_sheet` builds the investor one-pager of a return series from `performance.analyze`, and
`returns_table` the section of a fund, its benchmarks, and its excess over each, by period.
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
from quant_toolbox.report._returns import RETURN_PERIODS, returns_table
from quant_toolbox.report._runs import (
    INTERPRETATION,
    comparison_markdown,
    equity_chart,
    run_calendar_years,
    run_markdown,
)
from quant_toolbox.report._tear_sheet import HEADLINE, monthly_table, tear_sheet

__all__ = [
    "CHART_MODES",
    "COLOURS",
    "HEADLINE",
    "INTERPRETATION",
    "METRIC_KINDS",
    "RETURN_PERIODS",
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
    "monthly_table",
    "render_html",
    "render_markdown",
    "returns_table",
    "rolling_sharpe_chart",
    "run_calendar_years",
    "run_markdown",
    "tear_sheet",
    "to_dict",
    "underwater_chart",
]
