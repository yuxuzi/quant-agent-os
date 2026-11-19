"""The investor tear sheet and its monthly grid: known answers, and numbers equal to `analyze`."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from quant_toolbox import performance
from quant_toolbox.data import demo_returns
from quant_toolbox.report import (
    ChartBlock,
    TableBlock,
    TextBlock,
    monthly_table,
    render_markdown,
    tear_sheet,
    to_dict,
)


def test_the_monthly_grid_compounds_each_month_and_the_year() -> None:
    dates = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 3, 1), dt.date(2025, 2, 3)]
    table = monthly_table(dates, [0.10, 0.10, -0.05, 0.02])

    first, second = table.rows
    assert first[0] == "2024" and second[0] == "2025"
    assert first[1] == pytest.approx(0.21)  # January: 1.1 * 1.1 - 1
    assert first[2] is None  # no February return inside the year
    assert first[3] == pytest.approx(-0.05)
    assert first[13] is None  # a gap between the first and last month: no year total
    assert second[2] == pytest.approx(0.02) and second[13] == pytest.approx(0.02)
    assert [column.name for column in table.columns][1:13] == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]  # fmt: skip


def test_a_partial_year_totals_the_months_it_has() -> None:
    dates = [dt.date(2024, 11, 29), dt.date(2024, 12, 31)]
    (row,) = monthly_table(dates, [0.01, 0.02]).rows

    assert row[13] == pytest.approx(1.01 * 1.02 - 1.0)


def test_the_tear_sheet_shows_the_numbers_analyze_computes() -> None:
    table = demo_returns()
    fund, index = table.column("Macro"), table.column("Index")
    document = tear_sheet(table.dates, fund, 252, name="Macro", benchmark=index)
    analysis = performance.analyze(table.dates, fund, 252, benchmark=index)

    titles = [block.title for block in document.blocks]
    assert titles[:3] == ["Headline metrics", "Returns by horizon", "Against Benchmark"]
    assert titles[-1] == "Data notes and conventions"
    headline = document.blocks[0]
    assert isinstance(headline, TableBlock)
    sharpe = dict((row[0], row[1]) for row in headline.rows)["Sharpe ratio"]
    assert sharpe == f"{analysis.metrics['sharpe_ratio']:.2f}"
    horizons = document.blocks[1]
    assert isinstance(horizons, TableBlock)
    shown = dict(zip([column.name for column in horizons.columns], horizons.rows[0], strict=True))
    assert shown["Series"] == document.title.split(":")[0]
    for label, value in analysis.horizons.items():
        assert shown.get(label, value) == value
    assert sum(isinstance(block, ChartBlock) for block in document.blocks) == 3
    assert isinstance(document.blocks[-1], TextBlock)
    json.dumps(to_dict(document))  # JSON-safe
    assert "Sharpe standard error" in render_markdown(document)


def test_a_short_series_has_no_rolling_chart_and_no_benchmark_block() -> None:
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(30)]
    returns = np.linspace(-0.01, 0.012, 30)
    document = tear_sheet(dates, returns, 252)

    titles = [block.title for block in document.blocks]
    assert "Rolling one-year Sharpe" not in titles
    assert not any(title.startswith("Against") for title in titles)
    notes = document.blocks[-1]
    assert isinstance(notes, TextBlock)
    assert any("observations" in line or "short" in line.lower() for line in notes.lines)
