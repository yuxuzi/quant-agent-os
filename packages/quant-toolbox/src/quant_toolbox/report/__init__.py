"""Reports: documents of tables, charts, and text; renderers; chart builders; run reports.

A section of a report returns blocks and knows nothing about formats. `render_html` draws charts
as static SVG with pygal by default, or interactively with plotly (``charts="interactive"``, the
``interactive`` extra). Optional leaves: `quant_toolbox.report.svg` (pygal),
`quant_toolbox.report.interactive` (plotly), `quant_toolbox.report.excel` (openpyxl), and
`quant_toolbox.report.pdf` (fpdf2, the ``pdf`` extra: `render_pdf` and `write_pdf`).

`tear_sheet` builds the investor one-pager of a return series from `performance.analyze`, and
`returns_table` the section of a fund, its benchmarks, and its excess over each, by period.
`factsheet` builds a fund's monthly factsheet and `risk_report` a book's risk report, each from
sections a firm can also compose itself. `write_report` writes any document as HTML, Markdown,
JSON, PDF, or Excel by its file's suffix.
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
from quant_toolbox.report._factsheet import (
    breakdown_chart,
    breakdown_table,
    calendar_year_chart,
    calendar_year_table,
    facts_table,
    factsheet,
    holdings_table,
    risk_measures_table,
    rolling_chart,
)
from quant_toolbox.report._format import METRIC_KINDS, format_metric, label, metric_kind
from quant_toolbox.report._output import REPORT_FORMATS, write_report
from quant_toolbox.report._returns import RETURN_PERIODS, returns_table
from quant_toolbox.report._risk_report import (
    contribution_table,
    correlation_table,
    exposure_table,
    factor_table,
    group_exposure_table,
    liquidity_table,
    risk_report,
    stress_table,
    tail_risk_table,
)
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
    "REPORT_FORMATS",
    "RETURN_PERIODS",
    "Block",
    "Cell",
    "ChartBlock",
    "Column",
    "Document",
    "TableBlock",
    "TextBlock",
    "bar_chart",
    "breakdown_chart",
    "breakdown_table",
    "calendar_year_chart",
    "calendar_year_table",
    "comparison_markdown",
    "contribution_table",
    "correlation_table",
    "equity_chart",
    "equity_curve",
    "exposure_table",
    "factor_table",
    "facts_table",
    "factsheet",
    "format_cell",
    "format_metric",
    "group_exposure_table",
    "holdings_table",
    "label",
    "line_chart",
    "liquidity_table",
    "metric_kind",
    "monthly_table",
    "render_html",
    "render_markdown",
    "returns_table",
    "risk_measures_table",
    "risk_report",
    "rolling_chart",
    "rolling_sharpe_chart",
    "run_calendar_years",
    "run_markdown",
    "stress_table",
    "tail_risk_table",
    "tear_sheet",
    "to_dict",
    "underwater_chart",
    "write_report",
]
