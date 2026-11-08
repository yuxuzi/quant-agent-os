"""The report sections to choose from. Name them in a tear sheet in ``config.toml``.

A section takes the `Context` and returns blocks. Add your own in ``firm/sections.py``; a firm's
section with the same name as one here replaces it.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable

import numpy as np

from .context import Context
from .document import Block, ChartBlock, Column, TableBlock, TextBlock
from .firm.sections import SECTIONS as FIRM_SECTIONS
from .horizons import calendar_years as years_of
from .horizons import drawdowns as episodes
from .horizons import horizon_return, monthly_returns, underwater, wealth
from .metrics import (
    active_metrics,
    annualized_return,
    annualized_volatility,
    beta,
    hit_rate,
    max_drawdown,
    sharpe_ratio,
)
from .risk_metrics import calmar_ratio, expected_shortfall, sortino_ratio, value_at_risk

Section = Callable[[Context], list[Block]]
MONTHS = [calendar.month_abbr[number] for number in range(1, 13)]


def horizons(context: Context) -> list[Block]:
    """Returns by horizon, one row per book, plus the benchmark."""
    labels = context.settings.horizons
    rate = context.settings.periods_per_year

    def row(name: str, values: np.ndarray) -> list[float | str | None]:
        return [name, *(horizon_return(context.dates, values, label, rate) for label in labels)]

    rows = [row(book, context.returns(book)) for book in context.books.columns]
    if context.benchmark is not None:
        rows.append(row(context.benchmark_name, context.benchmark))
    return [
        TableBlock(
            title="Returns by horizon",
            columns=[Column("Book", "text"), *(Column(label) for label in labels)],
            rows=rows,
            note=f"As of {context.as_of}. Basis: {context.settings.basis}. Over one year: "
            "annualised. ITD: annualised once it covers a full year.",
        )
    ]


def calendar_years(context: Context) -> list[Block]:
    """Compounded return by calendar year, one row per book."""
    tables = {
        book: years_of(context.dates, context.returns(book)) for book in context.books.columns
    }
    years = sorted({year for table in tables.values() for year in table})
    return [
        TableBlock(
            title="Calendar years",
            columns=[Column("Book", "text"), *(Column(str(year)) for year in years)],
            rows=[[book, *(table.get(year) for year in years)] for book, table in tables.items()],
            note=f"Basis: {context.settings.basis}. A first or last year may be partial.",
        )
    ]


def monthly(context: Context) -> list[Block]:
    """A year-by-month return table for each book."""
    blocks: list[Block] = []
    for book in context.books.columns:
        months = monthly_returns(context.dates, context.returns(book))
        totals = years_of(context.dates, context.returns(book))
        rows = [
            [str(year), *(months.get((year, m)) for m in range(1, 13)), totals[year]]
            for year in sorted(totals)
        ]
        blocks.append(
            TableBlock(
                title=f"Monthly returns: {book}",
                columns=[
                    Column("Year", "text"),
                    *(Column(m) for m in MONTHS),
                    Column("Year total"),
                ],
                rows=rows,
                note=f"Basis: {context.settings.basis}.",
            )
        )
    return blocks


def risk(context: Context) -> list[Block]:
    """Return, volatility, ratios, drawdown, and tail risk for each book."""
    settings = context.settings
    rate, cutoff = settings.periods_per_year, settings.var_cutoff
    columns = [
        Column("Book", "text"),
        Column("Ann. return"),
        Column("Ann. vol"),
        Column("Sharpe", "ratio"),
        Column("Sortino", "ratio"),
        Column("Calmar", "ratio"),
        Column("Max drawdown"),
        Column(f"VaR {cutoff:.0%}"),
        Column(f"ES {cutoff:.0%}"),
        Column("Hit rate"),
    ]
    if context.benchmark is not None:
        columns += [
            Column("Beta", "ratio"),
            Column("Active return"),
            Column("Tracking error"),
            Column("Info ratio", "ratio"),
        ]
    rows: list[list[float | str | None]] = []
    for book in context.books.columns:
        values = context.returns(book)
        row: list[float | str | None] = [
            book,
            annualized_return(values, rate),
            annualized_volatility(values, rate),
            sharpe_ratio(values, rate, settings.risk_free),
            sortino_ratio(values, rate, settings.risk_free / rate),
            calmar_ratio(values, rate),
            max_drawdown(values),
            value_at_risk(values, cutoff),
            expected_shortfall(values, cutoff),
            hit_rate(values),
        ]
        if context.benchmark is not None:
            active = active_metrics(values, context.benchmark, rate)
            row += [
                beta(values, context.benchmark),
                active["active_return"],
                active["tracking_error"],
                active["information_ratio"],
            ]
        rows.append(row)
    note = f"{rate} bars a year; risk-free {settings.risk_free:.2%} a year. VaR and ES are per bar."
    if context.benchmark is not None:
        note += f" Against {context.benchmark_name}."
    return [TableBlock(title="Risk and ratios", columns=columns, rows=rows, note=note)]


def drawdowns(context: Context) -> list[Block]:
    """The deepest drawdown episodes for each book."""
    rows: list[list[float | str | None]] = []
    for book in context.books.columns:
        found = episodes(context.dates, context.returns(book), context.settings.top_drawdowns)
        for episode in found:
            rows.append(
                [
                    book,
                    episode.depth,
                    "start of data" if episode.peak is None else episode.peak.isoformat(),
                    episode.trough.isoformat(),
                    episode.recovered.isoformat() if episode.recovered else "not recovered",
                ]
            )
    return [
        TableBlock(
            title="Deepest drawdowns",
            columns=[
                Column("Book", "text"),
                Column("Depth"),
                Column("Peak", "text"),
                Column("Trough", "text"),
                Column("Recovered", "text"),
            ],
            rows=rows,
        )
    ]


def correlation(context: Context) -> list[Block]:
    """Correlation between books, on the bars where both have returns."""
    names = context.books.columns
    rows: list[list[float | str | None]] = []
    for left in names:
        row: list[float | str | None] = [left]
        for right in names:
            x, y = context.returns(left), context.returns(right)
            both = np.isfinite(x) & np.isfinite(y)
            flat = int(both.sum()) < 2 or np.std(x[both]) == 0.0 or np.std(y[both]) == 0.0
            row.append(None if flat else float(np.corrcoef(x[both], y[both])[0, 1]))
        rows.append(row)
    return [
        TableBlock(
            title="Correlation between books",
            columns=[Column("Book", "text"), *(Column(name, "ratio") for name in names)],
            rows=rows,
        )
    ]


def equity(context: Context) -> list[Block]:
    """Growth of 1, per book and for the benchmark."""
    series = {book: wealth(context.returns(book)).tolist() for book in context.books.columns}
    if context.benchmark is not None:
        series[context.benchmark_name] = wealth(context.benchmark).tolist()
    return [ChartBlock(title="Growth of 1", dates=context.dates, series=series)]


def underwater_chart(context: Context) -> list[Block]:
    """Drawdown from the running peak, per book."""
    series = {book: underwater(context.returns(book)).tolist() for book in context.books.columns}
    return [ChartBlock(title="Drawdown from peak", dates=context.dates, series=series)]


def rolling_sharpe(context: Context) -> list[Block]:
    """One-year rolling Sharpe ratio, per book."""
    rate = context.settings.periods_per_year
    series: dict[str, list[float]] = {}
    for book in context.books.columns:
        values = context.returns(book)
        rolled = [float("nan")] * len(values)
        for end in range(rate, len(values) + 1):
            rolled[end - 1] = sharpe_ratio(
                values[end - rate : end], rate, context.settings.risk_free
            )
        series[book] = rolled
    return [
        ChartBlock(
            title="Rolling one-year Sharpe",
            dates=context.dates,
            series=series,
            note=f"Over {rate} bars; blank until a full window exists.",
        )
    ]


def data_notes(context: Context) -> list[Block]:
    """What a reader should know about the data."""
    lines = context.data_notes or ["No gaps or unusual values were found."]
    return [TextBlock(title="Data notes", lines=lines)]


BUILT_IN: dict[str, Section] = {
    "horizons": horizons,
    "calendar_years": calendar_years,
    "monthly": monthly,
    "risk": risk,
    "drawdowns": drawdowns,
    "correlation": correlation,
    "equity": equity,
    "underwater": underwater_chart,
    "rolling_sharpe": rolling_sharpe,
    "data_notes": data_notes,
}


def available() -> dict[str, Section]:
    """Every section that can be chosen: the built-in ones and the firm's own."""
    return {**BUILT_IN, **FIRM_SECTIONS}


def describe(section: Section) -> str:
    """The first line of a section's docstring."""
    return (section.__doc__ or "").strip().splitlines()[0] if section.__doc__ else ""
