"""An investor tear sheet: one return series, optionally against a benchmark, as a `Document`.

Every number comes from `quant_toolbox.performance.analyze`, so the tear sheet, a tool's output,
and a notebook show the same figures under the same conventions. It renders like any document:
`render_html` (static SVG charts by default), `render_markdown`, or `report.excel.write_workbook`.

Blocks, in order: headline metrics, returns by horizon, the benchmark comparison (with one), the
monthly return grid, the deepest drawdowns, the return distribution, growth of 1, drawdown from
peak, rolling one-year Sharpe, and the data notes and conventions.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Sequence

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import Analysis, Series, analyze, monthly_returns, rolling_sharpe
from quant_toolbox.report._charts import equity_curve, line_chart, underwater_chart
from quant_toolbox.report._document import Block, Cell, Column, Document, TableBlock, TextBlock
from quant_toolbox.report._format import format_metric, label

MONTHS = tuple(calendar.month_abbr[1:])
HEADLINE = (
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown",
    "value_at_risk",
    "hit_rate",
)


def monthly_table(
    dates: Sequence[dt.date], returns: Series, *, title: str = "Monthly returns"
) -> TableBlock:
    """Compounded returns by month, one row per year, with the year's compounded total last.

    A month with no return is n/a, and so is a year with any month missing between its first
    and last month with a return.
    """
    values = np.asarray(returns, dtype=np.float64).ravel()
    months = monthly_returns(list(dates), values)
    rows: list[list[Cell]] = []
    for year in sorted({year for year, _ in months}):
        cells: list[Cell] = [months.get((year, month)) for month in range(1, 13)]
        held = [index for index, cell in enumerate(cells) if cell is not None]
        inside = cells[held[0] : held[-1] + 1]
        total: float | None = None
        if all(cell is not None for cell in inside):
            total = float(np.prod([1.0 + float(cell) for cell in inside if cell is not None])) - 1.0
        rows.append([str(year), *cells, total])
    columns = [Column("Year", "text"), *(Column(month) for month in MONTHS), Column("Year total")]
    return TableBlock(title, columns, rows, note="Compounded from per-bar returns.")


def tear_sheet(
    dates: Sequence[dt.date],
    returns: Series,
    periods_per_year: int,
    *,
    name: str = "Fund",
    benchmark: Series | None = None,
    benchmark_name: str = "Benchmark",
    risk_free: float = 0.0,
    backend: str | None = None,
    title: str | None = None,
) -> Document:
    """The investor tear sheet of ``returns`` (one per date), against ``benchmark`` if given."""
    analysis = analyze(
        dates, returns, periods_per_year, benchmark=benchmark, risk_free=risk_free, backend=backend
    )
    fund = np.asarray(returns, dtype=np.float64).ravel()
    days = list(dates)
    series: dict[str, FloatArray] = {name: fund}
    if benchmark is not None:
        series[benchmark_name] = np.asarray(benchmark, dtype=np.float64).ravel()
    blocks: list[Block] = [
        _metrics_table("Headline metrics", {key: analysis.metrics[key] for key in HEADLINE}),
        _horizons_table(analysis),
    ]
    if analysis.benchmark is not None:
        blocks.append(_metrics_table(f"Against {benchmark_name}", analysis.benchmark))
    blocks += [
        monthly_table(days, fund, title=f"{name}: monthly returns"),
        _drawdowns_table(analysis),
        _distribution_table(analysis),
        equity_curve(days, series),
        underwater_chart(days, {name: fund}),
    ]
    if len(days) > periods_per_year:
        rolled = rolling_sharpe(fund, periods_per_year, periods_per_year, risk_free)
        blocks.append(
            line_chart(
                "Rolling one-year Sharpe",
                days,
                {name: rolled},
                note=f"Over {periods_per_year} bars; blank until a full window, or over a gap.",
            )
        )
    blocks.append(_notes(analysis))
    subtitle = (
        f"{analysis.observations} returns to {analysis.as_of.isoformat()}, "
        f"{periods_per_year} per year; metrics by the {analysis.backend} backend"
    )
    return Document(title or f"{name}: tear sheet", subtitle=subtitle, blocks=blocks)


def _metrics_table(title: str, metrics: dict[str, float]) -> TableBlock:
    rows: list[list[Cell]] = [[label(key), _shown(key, value)] for key, value in metrics.items()]
    return TableBlock(title, [Column("Metric", "text"), Column("Value", "text")], rows)


def _shown(key: str, value: float) -> Cell:
    """A metric as display text, so one column can hold percentages and ratios."""
    return format_metric(key, value, ratio_digits=2)


def _horizons_table(analysis: Analysis) -> TableBlock:
    labels = list(analysis.horizons)
    row: list[Cell] = [analysis.horizons[key] for key in labels]
    return TableBlock(
        "Returns by horizon",
        [Column(key) for key in labels],
        [row],
        note="Over one year annualised; n/a where the series does not cover the horizon.",
    )


def _drawdowns_table(analysis: Analysis) -> TableBlock:
    rows: list[list[Cell]] = [
        [
            "start" if episode.peak is None else episode.peak.isoformat(),
            episode.trough.isoformat(),
            "not yet" if episode.recovered is None else episode.recovered.isoformat(),
            episode.depth,
            episode.length,
        ]
        for episode in analysis.drawdowns
    ]
    columns = [
        Column("Peak", "text"),
        Column("Trough", "text"),
        Column("Recovered", "text"),
        Column("Depth"),
        Column("Bars", "number"),
    ]
    return TableBlock("Deepest drawdowns", columns, rows)


def _distribution_table(analysis: Analysis) -> TableBlock:
    shape = analysis.distribution
    figures = {
        "best": shape.best,
        "worst": shape.worst,
        "average_gain": shape.average_gain,
        "average_loss": shape.average_loss,
        "gain_loss_ratio": shape.gain_loss_ratio,
        "profit_factor": shape.profit_factor,
        "skewness": shape.skewness,
        "excess_kurtosis": shape.excess_kurtosis,
        **{
            f"sharpe_{key}" if key == "standard_error" else key: value
            for key, value in analysis.sharpe_inference.items()
        },
    }
    return _metrics_table("Distribution and Sharpe confidence", figures)


def _notes(analysis: Analysis) -> TextBlock:
    lines = list(analysis.notes) or ["No data notes."]
    lines += [f"{label(key)}: {text}." for key, text in analysis.conventions.items()]
    return TextBlock("Data notes and conventions", lines)
