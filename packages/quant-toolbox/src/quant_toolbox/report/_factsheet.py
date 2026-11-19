"""A monthly factsheet: a fund's facts, returns, risk, holdings, and breakdown, as a `Document`.

The layout follows the common elements of UCITS and hedge fund factsheets (facts, calendar-year
returns, risk measures against a benchmark, rolling risk, top holdings, and exposure by group).
Every number comes from `quant_toolbox.performance`, under its conventions, so a factsheet shows
the same figures as a tear sheet or a notebook. It renders like any document: `render_html`,
`render_markdown`, `render_pdf`, or `write_report`.

Blocks, in order: the facts (with any given), the returns by horizon, calendar-year returns (a
table and a chart), growth of 1, risk measures over trailing windows, rolling volatility, the
top holdings, and the breakdown by group (a table and a chart). A section whose inputs were not
given is left out.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import (
    Series,
    annualized_volatility,
    benchmark_metrics,
    calendar_years,
    capture_ratios,
    horizon_return,
    horizon_start,
    max_drawdown,
    rolling_beta,
    rolling_volatility,
    sharpe_ratio,
    sortino_ratio,
)
from quant_toolbox.report._charts import bar_chart, equity_curve, line_chart
from quant_toolbox.report._document import Block, Cell, ChartBlock, Column, Document, TableBlock
from quant_toolbox.report._format import format_metric, label
from quant_toolbox.report._returns import returns_table

OTHER = "Other"
ROLLING_MEASURES = ("volatility", "beta")
RISK_KEYS = (
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
)
BENCHMARK_KEYS = (
    "beta",
    "alpha",
    "correlation",
    "r_squared",
    "tracking_error",
    "information_ratio",
    "up_capture",
    "down_capture",
)
# Capture ratios are read as percentages of the benchmark's move, as factsheets print them.
RISK_KINDS = {"up_capture": "percent", "down_capture": "percent"}


def facts_table(facts: Mapping[str, Cell], *, title: str = "Fund facts") -> TableBlock:
    """Fund facts as text: a name, a date, a fee, a currency. None is shown as n/a."""
    rows: list[list[Cell]] = [[name, value] for name, value in facts.items()]
    return TableBlock(title, [Column("Fact", "text"), Column("Value", "text")], rows)


def calendar_year_table(
    dates: Sequence[dt.date],
    series: Mapping[str, Series],
    *,
    excess_over: str | None = None,
    title: str = "Calendar-year returns",
) -> TableBlock:
    """One row per series, one column per calendar year, compounded from the per-bar returns.

    With ``excess_over`` (the name of one series, usually the fund), a row per other series gives
    the excess of that series' year over it. The first and last years may be partial.
    """
    if excess_over is not None and excess_over not in series:
        raise ValueError(f"No series {excess_over!r}; the table has {', '.join(series)}.")
    years, grid = _calendar_grid(dates, series)
    rows: list[list[Cell]] = [[name, *values] for name, values in grid.items()]
    if excess_over is not None:
        base = grid[excess_over]
        for name, other in grid.items():
            if name != excess_over:
                rows.append([f"{excess_over} over {name}", *_difference(base, other)])
    return TableBlock(
        title,
        [Column("Series", "text"), *(Column(str(year)) for year in years)],
        rows,
        note=(
            "Compounded from per-bar returns. The first and last years may be partial; n/a where "
            "a series has no return in a year. An excess row is the difference of two years."
        ),
        shade=True,
    )


def calendar_year_chart(
    dates: Sequence[dt.date],
    series: Mapping[str, Series],
    *,
    title: str = "Calendar-year returns",
) -> ChartBlock:
    """Calendar-year returns as bars, one bar per series in each year."""
    years, grid = _calendar_grid(dates, series)
    values = {
        name: [math.nan if value is None else value for value in column]
        for name, column in grid.items()
    }
    return bar_chart(title, [str(year) for year in years], values, percent=True)


def risk_measures_table(
    dates: Sequence[dt.date],
    returns: Series,
    periods_per_year: int,
    *,
    benchmark: Series | None = None,
    windows: Sequence[int] = (1, 3, 5),
    risk_free: float = 0.0,
    calendar: object = None,
    title: str = "Risk measures",
) -> TableBlock:
    """Risk measures over trailing windows of ``windows`` years, one column per window.

    A window is the horizon of the same name in `horizon_return` (``1Y``, ``3Y``, ...): the
    returns dated after `horizon_start`, and its annualized return is `horizon_return`'s, so it
    matches the returns table. A window the series does not cover is n/a for every row. Beta,
    alpha, correlation, R squared, tracking error, information ratio, and capture ratios appear
    only with a ``benchmark``. Returns are per bar and annualized; the risk-free rate is annual and
    enters Sharpe and alpha; Sortino is against zero. ``calendar`` is `horizon_return`'s.
    """
    days = list(dates)
    fund = _values("The fund", returns, len(days))
    market = None if benchmark is None else _values("The benchmark", benchmark, len(days))
    if any(years < 1 for years in windows):
        raise ValueError(f"A window is at least one year, not {min(windows)}.")
    keys = RISK_KEYS + (BENCHMARK_KEYS if market is not None else ())
    measured: list[dict[str, float] | None] = []
    for years in windows:
        horizon = f"{years}Y"
        growth = horizon_return(days, fund, horizon, periods_per_year, calendar=calendar)
        start = horizon_start(horizon, days[-1])
        if growth is None or start is None:
            measured.append(None)
            continue
        inside = np.array([day > start for day in days])
        window_market = None if market is None else market[inside]
        window = _window_measures(fund[inside], window_market, periods_per_year, risk_free)
        measured.append(window | {"annualized_return": growth})
    rows: list[list[Cell]] = []
    for key in keys:
        cells: list[Cell] = [label(key)]
        for found in measured:
            value = None if found is None else found.get(key)
            cells.append(format_metric(key, value, ratio_digits=2, kinds=RISK_KINDS))
        rows.append(cells)
    columns = [Column("Measure", "text"), *(Column(f"{years}Y", "text") for years in windows)]
    note = (
        f"Trailing horizons, as in the returns table; n/a where the series does not cover one. "
        f"Annualized from per-bar returns; risk-free {risk_free:.2%} in Sharpe and alpha."
    )
    if market is not None:
        note += " Beta, alpha, and capture are against the benchmark."
    return TableBlock(title, columns, rows, note=note)


def rolling_chart(
    dates: Sequence[dt.date],
    series: Mapping[str, Series],
    periods_per_year: int,
    *,
    measure: str = "volatility",
    benchmark: Series | None = None,
    window: int | None = None,
    title: str | None = None,
) -> ChartBlock:
    """Rolling volatility, or rolling beta against ``benchmark``, over a trailing ``window``.

    The window is one year of bars unless given. A value is blank until a full window exists.
    """
    if measure not in ROLLING_MEASURES:
        raise ValueError(f"measure must be one of {', '.join(ROLLING_MEASURES)}, not {measure!r}.")
    days = list(dates)
    size = periods_per_year if window is None else window
    rolled: dict[str, FloatArray] = {}
    if measure == "volatility":
        for name, returns in series.items():
            values = _values(name, returns, len(days))
            rolled[name] = rolling_volatility(values, size, periods_per_year)
        percent = True
    else:
        if benchmark is None:
            raise ValueError("A rolling beta needs a benchmark to measure against.")
        market = _values("The benchmark", benchmark, len(days))
        for name, returns in series.items():
            rolled[name] = rolling_beta(_values(name, returns, len(days)), market, size)
        percent = False
    return line_chart(
        title or f"Rolling {measure}",
        days,
        rolled,
        note=f"Over {size} bars; blank until a full window exists.",
        percent=percent,
    )


def holdings_table(
    weights: Mapping[str, float], *, top: int = 10, title: str = "Top holdings"
) -> TableBlock:
    """The ``top`` holdings by absolute weight, largest first, with their share of gross exposure.

    A note says how many holdings are shown of how many, and what share of gross exposure they
    cover, when the table is trimmed.
    """
    if top < 1:
        raise ValueError(f"top must be at least 1, not {top}.")
    ranked = sorted(weights.items(), key=lambda item: (-abs(item[1]), item[0]))
    shown = ranked[:top]
    rows: list[list[Cell]] = [[name, float(weight)] for name, weight in shown]
    note = ""
    if len(ranked) > top:
        gross = sum(abs(weight) for _, weight in ranked)
        covered = sum(abs(weight) for _, weight in shown)
        share = f"{covered / gross:.1%} of gross" if gross else "no gross exposure"
        note = f"{len(shown)} of {len(ranked)} holdings; {share}."
    return TableBlock(
        title, [Column("Holding", "text"), Column("Weight", "percent")], rows, note=note
    )


def breakdown_table(
    weights: Mapping[str, float],
    groups: Mapping[str, str],
    *,
    benchmark_weights: Mapping[str, float] | None = None,
    title: str = "Breakdown",
) -> TableBlock:
    """Weights summed by group, largest first; an asset without a group is in "Other".

    With ``benchmark_weights``, the table adds the benchmark's sums and the active weight (the
    fund less the benchmark) for each group.
    """
    order, fund, bench = _group_sums(weights, groups, benchmark_weights)
    if benchmark_weights is None:
        columns = [Column("Group", "text"), Column("Weight", "percent")]
        rows: list[list[Cell]] = [[group, fund[group]] for group in order]
        note = f"Weights summed by group; assets without a group are in {OTHER}."
    else:
        columns = [
            Column("Group", "text"),
            Column("Fund", "percent"),
            Column("Benchmark", "percent"),
            Column("Active", "percent"),
        ]
        rows = [[group, fund[group], bench[group], fund[group] - bench[group]] for group in order]
        note = (
            f"Weights summed by group; assets without a group are in {OTHER}. "
            "Active is the fund less the benchmark."
        )
    return TableBlock(title, columns, rows, note=note)


def breakdown_chart(
    weights: Mapping[str, float],
    groups: Mapping[str, str],
    *,
    benchmark_weights: Mapping[str, float] | None = None,
    title: str = "Breakdown",
) -> ChartBlock:
    """The group sums of `breakdown_table` as bars: the fund, and the benchmark if given."""
    order, fund, bench = _group_sums(weights, groups, benchmark_weights)
    series: dict[str, list[float]] = {"Fund": [fund[group] for group in order]}
    if benchmark_weights is not None:
        series["Benchmark"] = [bench[group] for group in order]
    return bar_chart(title, order, series, percent=True)


def factsheet(
    dates: Sequence[dt.date],
    returns: Series,
    periods_per_year: int,
    *,
    name: str = "Fund",
    benchmark: Series | None = None,
    benchmark_name: str = "Benchmark",
    compare: Mapping[str, Series] | None = None,
    facts: Mapping[str, Cell] | None = None,
    weights: Mapping[str, float] | None = None,
    groups: Mapping[str, str] | None = None,
    benchmark_weights: Mapping[str, float] | None = None,
    disclaimer: str = "",
    risk_free: float = 0.0,
    calendar: object = None,
    title: str | None = None,
) -> Document:
    """The monthly factsheet of ``returns`` (one per date), as a document.

    Only the fund's returns are needed. The benchmark, the ``compare`` series, the facts, the
    holdings (``weights``), and the breakdown (``weights`` with ``groups``) each add their section
    when given. ``disclaimer`` is the document's footer, shown after every block.
    """
    days = list(dates)
    fund = _values(name, returns, len(days))
    series: dict[str, FloatArray] = {name: fund}
    market: FloatArray | None = None
    if benchmark is not None:
        market = _values(benchmark_name, benchmark, len(days))
        series[benchmark_name] = market
    for other, values in (compare or {}).items():
        if other in series:
            raise ValueError(
                f"{other!r} names two series in the factsheet; give each its own name."
            )
        series[other] = _values(other, values, len(days))
    rolling = {name: fund} | ({benchmark_name: market} if market is not None else {})
    blocks: list[Block] = []
    if facts:
        blocks.append(facts_table(facts))
    blocks += [
        returns_table(days, series, periods_per_year, excess_over=name, calendar=calendar),
        calendar_year_table(days, series, excess_over=name),
        calendar_year_chart(days, series),
        equity_curve(days, series),
        risk_measures_table(
            days, fund, periods_per_year, benchmark=market, risk_free=risk_free, calendar=calendar
        ),
        rolling_chart(days, rolling, periods_per_year, title="Rolling one-year volatility"),
    ]
    if weights:
        blocks.append(holdings_table(weights))
        if groups:
            blocks.append(
                breakdown_table(
                    weights, groups, benchmark_weights=benchmark_weights, title="Breakdown"
                )
            )
            blocks.append(
                breakdown_chart(
                    weights, groups, benchmark_weights=benchmark_weights, title="Breakdown"
                )
            )
    subtitle = (
        f"{len(days)} returns to {days[-1].isoformat()}, {periods_per_year} per year"
        if days
        else ""
    )
    return Document(
        title or f"{name}: factsheet", subtitle=subtitle, blocks=blocks, footer=disclaimer
    )


def _values(name: str, returns: Series, count: int) -> FloatArray:
    """The returns as an array with one value per date, or a refusal naming the series."""
    values = np.asarray(returns, dtype=np.float64).ravel()
    if values.size != count:
        raise ValueError(f"{name} has {values.size} returns for {count} dates.")
    return values


def _calendar_grid(
    dates: Sequence[dt.date], series: Mapping[str, Series]
) -> tuple[list[int], dict[str, list[float | None]]]:
    """The calendar years of the dates, and each series' compounded return in each year."""
    days = list(dates)
    if not days:
        raise ValueError("No dates to report returns over.")
    years = sorted({day.year for day in days})
    grid: dict[str, list[float | None]] = {}
    for name, returns in series.items():
        by_year = calendar_years(days, _values(name, returns, len(days)))
        grid[name] = [by_year.get(year) for year in years]
    return years, grid


def _difference(left: list[float | None], right: list[float | None]) -> list[Cell]:
    return [None if a is None or b is None else a - b for a, b in zip(left, right, strict=True)]


def _window_measures(
    fund: FloatArray,
    market: FloatArray | None,
    periods_per_year: int,
    risk_free: float,
) -> dict[str, float]:
    """Every risk measure of one window; the benchmark's only when there is one."""
    measures = {
        "annualized_volatility": annualized_volatility(fund, periods_per_year),
        "sharpe_ratio": sharpe_ratio(fund, periods_per_year, risk_free),
        "sortino_ratio": sortino_ratio(fund, periods_per_year),
        "max_drawdown": max_drawdown(fund),
    }
    if market is not None:
        against = benchmark_metrics(fund, market, periods_per_year, risk_free)
        captures = capture_ratios(fund, market)
        measures |= {
            "beta": against["beta"],
            "alpha": against["alpha"],
            "correlation": captures["correlation"],
            "r_squared": captures["correlation"] ** 2,
            "tracking_error": against["tracking_error"],
            "information_ratio": against["information_ratio"],
            "up_capture": captures["up_capture"],
            "down_capture": captures["down_capture"],
        }
    return measures


def _group_sums(
    weights: Mapping[str, float],
    groups: Mapping[str, str],
    benchmark_weights: Mapping[str, float] | None,
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    """The groups, largest absolute sum first, and each book's sum per group."""
    fund = _sum_by_group(weights, groups)
    bench = _sum_by_group(benchmark_weights or {}, groups)
    names = set(fund) | set(bench)
    order = sorted(
        names,
        key=lambda group: (-max(abs(fund.get(group, 0.0)), abs(bench.get(group, 0.0))), group),
    )
    return (
        order,
        {group: fund.get(group, 0.0) for group in order},
        {group: bench.get(group, 0.0) for group in order},
    )


def _sum_by_group(weights: Mapping[str, float], groups: Mapping[str, str]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for asset, weight in weights.items():
        group = groups.get(asset, OTHER)
        totals[group] = totals.get(group, 0.0) + float(weight)
    return totals
