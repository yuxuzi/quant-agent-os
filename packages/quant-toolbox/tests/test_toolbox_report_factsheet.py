"""The monthly factsheet: known answers for each section, and renders in every format."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_toolbox.data import demo_returns
from quant_toolbox.performance import annualized_volatility, horizon_start
from quant_toolbox.report import (
    ChartBlock,
    Document,
    TableBlock,
    breakdown_chart,
    breakdown_table,
    calendar_year_chart,
    calendar_year_table,
    facts_table,
    factsheet,
    holdings_table,
    render_html,
    render_markdown,
    returns_table,
    risk_measures_table,
    rolling_chart,
)


def _books() -> tuple[list[dt.date], dict[str, np.ndarray]]:
    table = demo_returns(800)
    return table.dates, {name: table.column(name) for name in ("Macro", "Index", "Stat Arb")}


def _titles(document: Document) -> list[str]:
    return [block.title for block in document.blocks]


def _row(table: TableBlock, name: str) -> list[object]:
    return next(row for row in table.rows if row[0] == name)


def test_calendar_years_compound_the_daily_returns_by_hand() -> None:
    dates = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)]
    dates += [dt.date(2025, 1, 2), dt.date(2025, 1, 3)]
    fund = [0.01, 0.02, -0.01, 0.05, 0.05]
    bench = [0.00, 0.01, 0.01, 0.02, -0.02]
    table = calendar_year_table(dates, {"Fund": fund, "Bench": bench})

    assert [column.name for column in table.columns] == ["Series", "2024", "2025"]
    fund_row, bench_row = table.rows
    assert fund_row[1] == pytest.approx(1.01 * 1.02 * 0.99 - 1.0)
    assert fund_row[2] == pytest.approx(1.05 * 1.05 - 1.0)
    assert bench_row[1] == pytest.approx(1.00 * 1.01 * 1.01 - 1.0)
    assert bench_row[2] == pytest.approx(1.02 * 0.98 - 1.0)
    assert table.shade is True


def test_calendar_year_excess_rows_are_differences_of_the_years() -> None:
    dates, books = _books()
    table = calendar_year_table(dates, books, excess_over="Macro")
    macro = _row(table, "Macro")
    index = _row(table, "Index")
    excess = _row(table, "Macro over Index")
    assert [row[0] for row in table.rows] == [
        "Macro",
        "Index",
        "Stat Arb",
        "Macro over Index",
        "Macro over Stat Arb",
    ]
    for got, mine, theirs in zip(excess[1:], macro[1:], index[1:], strict=True):
        assert got == pytest.approx(mine - theirs)  # type: ignore[operator]


def test_calendar_year_table_and_chart_check_their_inputs() -> None:
    dates, books = _books()
    with pytest.raises(ValueError, match="No series 'Fund'"):
        calendar_year_table(dates, books, excess_over="Fund")
    with pytest.raises(ValueError, match="has 3 returns"):
        calendar_year_chart(dates, {"Short": np.zeros(3)})
    with pytest.raises(ValueError, match="No dates"):
        calendar_year_table([], books)


def test_calendar_year_chart_is_one_bar_per_year_as_percentages() -> None:
    dates, books = _books()
    chart = calendar_year_chart(dates, {"Macro": books["Macro"]})
    years = sorted({day.year for day in dates})
    assert chart.kind == "bar"
    assert chart.labels == [str(year) for year in years]
    assert chart.percent is True
    assert len(chart.series["Macro"]) == len(years)


def test_risk_measures_are_n_a_for_a_window_longer_than_the_data() -> None:
    dates, books = _books()  # 800 bars: three years of 252 fit, five do not
    table = risk_measures_table(dates, books["Macro"], 252, benchmark=books["Index"])
    assert [column.name for column in table.columns] == ["Measure", "1Y", "3Y", "5Y"]
    for row in table.rows:
        assert row[3] == "n/a"
        assert row[1] != "n/a" and row[2] != "n/a"


def test_risk_measures_of_a_fund_against_itself_are_one_and_zero() -> None:
    dates, books = _books()
    fund = books["Macro"]
    table = risk_measures_table(dates, fund, 252, benchmark=fund, windows=(1,))
    assert _row(table, "Beta")[1] == "1.00"
    assert _row(table, "Correlation")[1] == "1.00"
    assert _row(table, "R squared")[1] == "1.00"
    assert _row(table, "Up capture")[1] == "100.00%"
    assert _row(table, "Down capture")[1] == "100.00%"
    assert _row(table, "Tracking error")[1] == "0.00%"


def test_risk_measures_without_a_benchmark_have_only_the_fund_rows() -> None:
    dates, books = _books()
    table = risk_measures_table(dates, books["Macro"], 252)
    assert [row[0] for row in table.rows] == [
        "Annualized return",
        "Annualized volatility",
        "Sharpe ratio",
        "Sortino ratio",
        "Max drawdown",
    ]


def test_risk_measures_cover_the_horizons_of_the_returns_table() -> None:
    dates, books = _books()
    fund = books["Macro"]
    risk = risk_measures_table(dates, fund, 252, windows=(1, 3))
    horizons = returns_table(dates, {"Macro": fund}, 252, periods=("1Y", "3Y"))
    assert _row(risk, "Annualized return")[1:] == [f"{value:.2%}" for value in horizons.rows[0][1:]]
    start = horizon_start("1Y", dates[-1])
    assert start is not None
    inside = np.array([day > start for day in dates])
    volatility = annualized_volatility(fund[inside], 252)
    assert _row(risk, "Annualized volatility")[1] == f"{volatility:.2%}"


def test_rolling_chart_refuses_beta_without_a_benchmark_and_unknown_measures() -> None:
    dates, books = _books()
    with pytest.raises(ValueError, match="needs a benchmark"):
        rolling_chart(dates, {"Macro": books["Macro"]}, 252, measure="beta")
    with pytest.raises(ValueError, match="volatility, beta"):
        rolling_chart(dates, {"Macro": books["Macro"]}, 252, measure="sharpe")


def test_rolling_volatility_and_beta_charts() -> None:
    dates, books = _books()
    volatility = rolling_chart(dates, {"Macro": books["Macro"]}, 252)
    assert volatility.title == "Rolling volatility"
    assert volatility.percent is True
    assert np.isnan(volatility.series["Macro"][0])
    assert np.isfinite(volatility.series["Macro"][-1])

    beta = rolling_chart(
        dates, {"Macro": books["Macro"]}, 252, measure="beta", benchmark=books["Index"], window=60
    )
    assert beta.percent is False and len(beta.dates) == len(dates)


def test_holdings_are_trimmed_and_sorted_by_absolute_weight() -> None:
    weights = {"A": 0.10, "B": -0.50, "C": 0.20, "D": 0.05}
    table = holdings_table(weights, top=2)
    assert [row[0] for row in table.rows] == ["B", "C"]
    assert table.rows[0][1] == -0.50
    assert table.note == "2 of 4 holdings; 82.4% of gross."
    assert holdings_table(weights).note == ""
    with pytest.raises(ValueError, match="at least 1"):
        holdings_table(weights, top=0)


def test_breakdown_sums_groups_and_puts_unclassed_assets_in_other() -> None:
    weights = {"AAPL": 0.30, "MSFT": 0.20, "BND": 0.50, "XYZ": 0.10}
    groups = {"AAPL": "Tech", "MSFT": "Tech", "BND": "Bonds"}
    table = breakdown_table(weights, groups)
    sums = {row[0]: row[1] for row in table.rows}
    assert sums == pytest.approx({"Tech": 0.50, "Bonds": 0.50, "Other": 0.10})
    assert [column.name for column in table.columns] == ["Group", "Weight"]


def test_breakdown_active_is_fund_less_benchmark() -> None:
    weights = {"AAPL": 0.30, "MSFT": 0.20, "BND": 0.50, "XYZ": 0.10}
    groups = {"AAPL": "Tech", "MSFT": "Tech", "BND": "Bonds"}
    bench = {"AAPL": 0.20, "BND": 0.80}
    table = breakdown_table(weights, groups, benchmark_weights=bench)
    assert [column.name for column in table.columns] == ["Group", "Fund", "Benchmark", "Active"]
    assert table.shade is False
    tech, bonds, other = _row(table, "Tech"), _row(table, "Bonds"), _row(table, "Other")
    assert tech[1:] == pytest.approx([0.50, 0.20, 0.30])  # type: ignore[arg-type]
    assert bonds[1:] == pytest.approx([0.50, 0.80, -0.30])  # type: ignore[arg-type]
    assert other[1:] == pytest.approx([0.10, 0.00, 0.10])  # type: ignore[arg-type]


def test_breakdown_chart_has_the_fund_and_the_benchmark_bars() -> None:
    weights = {"AAPL": 0.30, "BND": 0.50}
    groups = {"AAPL": "Tech", "BND": "Bonds"}
    fund_only = breakdown_chart(weights, groups)
    assert set(fund_only.series) == {"Fund"}
    chart = breakdown_chart(weights, groups, benchmark_weights={"BND": 1.0})
    assert chart.kind == "bar" and chart.percent is True
    assert set(chart.series) == {"Fund", "Benchmark"}
    assert chart.labels[0] == "Bonds"  # the larger absolute sum first
    assert chart.series["Benchmark"] == [1.0, 0.0]


def test_facts_table_shows_none_as_n_a() -> None:
    table = facts_table({"Inception": "2019-03-01", "Fee": 0.0125, "Hedged": None})
    assert [column.name for column in table.columns] == ["Fact", "Value"]
    assert table.rows[2] == ["Hedged", None]
    assert "n/a" in render_markdown(Document("Facts", blocks=[table]))


def test_a_factsheet_with_only_returns_has_the_return_and_risk_sections() -> None:
    dates, books = _books()
    document = factsheet(dates, books["Macro"], 252)
    assert _titles(document) == [
        "Returns by horizon",
        "Calendar-year returns",
        "Calendar-year returns",
        "Growth of 1",
        "Risk measures",
        "Rolling one-year volatility",
    ]
    assert document.title == "Fund: factsheet"
    assert document.subtitle.startswith(f"{len(dates)} returns to {dates[-1].isoformat()}")


def test_a_factsheet_includes_each_section_only_when_its_inputs_are_given() -> None:
    dates, books = _books()
    groups = {"Macro": "Hedge", "Index": "Equity"}
    document = factsheet(
        dates,
        books["Macro"],
        252,
        name="Macro",
        benchmark=books["Index"],
        benchmark_name="Index",
        compare={"Peer": books["Stat Arb"]},
        facts={"Currency": "USD"},
        weights={"Macro": 0.6, "Index": 0.4},
        groups=groups,
        benchmark_weights={"Index": 1.0},
        disclaimer="Past performance is no guide.",
        title="Macro fund, November",
    )
    titles = _titles(document)
    assert titles[0] == "Fund facts"
    assert titles.count("Top holdings") == 1
    assert titles.count("Breakdown") == 2
    assert document.title == "Macro fund, November"
    assert document.footer == "Past performance is no guide."
    returns = document.blocks[1]
    assert isinstance(returns, TableBlock)
    assert [row[0] for row in returns.rows][:3] == ["Macro", "Index", "Peer"]
    assert "Macro over Peer" in [row[0] for row in returns.rows]
    risk = next(block for block in document.blocks if block.title == "Risk measures")
    assert isinstance(risk, TableBlock)
    assert "Beta" in [row[0] for row in risk.rows]


def test_a_factsheet_without_groups_has_holdings_but_no_breakdown() -> None:
    dates, books = _books()
    document = factsheet(dates, books["Macro"], 252, weights={"Macro": 1.0})
    assert "Top holdings" in _titles(document)
    assert "Breakdown" not in _titles(document)


def test_a_factsheet_rejects_a_series_of_the_wrong_length_and_a_repeated_name() -> None:
    dates, books = _books()
    with pytest.raises(ValueError, match="has 3 returns"):
        factsheet(dates, books["Macro"], 252, benchmark=np.zeros(3))
    with pytest.raises(ValueError, match="names two series"):
        factsheet(dates, books["Macro"], 252, benchmark=books["Index"],
                  compare={"Benchmark": books["Index"]})  # fmt: skip


def test_a_factsheet_renders_as_html_markdown_and_pdf(tmp_path: object) -> None:
    pytest.importorskip("pygal")
    dates, books = _books()
    document = factsheet(
        dates,
        books["Macro"],
        252,
        name="Macro",
        benchmark=books["Index"],
        benchmark_name="Index",
        facts={"Currency": "USD"},
        weights={"Macro": 0.6, "Index": 0.4},
        groups={"Macro": "Hedge", "Index": "Equity"},
        disclaimer="Past performance is no guide.",
    )
    html = render_html(document)
    assert "<h2>Risk measures</h2>" in html and "Past performance" in html
    assert html.count("<svg") >= 4  # the charts are drawn
    assert "| Measure |" in render_markdown(document)

    pytest.importorskip("fpdf")
    from quant_toolbox.report.pdf import render_pdf

    data = render_pdf(document)
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")


def test_the_rolling_chart_is_a_chart_block() -> None:
    dates, books = _books()
    chart = rolling_chart(dates, {"Macro": books["Macro"]}, 252, title="Vol")
    assert isinstance(chart, ChartBlock) and chart.kind == "line"
