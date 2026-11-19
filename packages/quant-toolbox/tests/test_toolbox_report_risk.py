"""Known answers for the risk report's sections, and a risk report on the demo book."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from quant_toolbox.data import demo_book
from quant_toolbox.performance import (
    expected_shortfall,
    parametric_expected_shortfall,
    parametric_var,
    value_at_risk,
)
from quant_toolbox.report import (
    Document,
    TableBlock,
    contribution_table,
    correlation_table,
    exposure_table,
    factor_table,
    group_exposure_table,
    liquidity_table,
    render_html,
    render_markdown,
    risk_report,
    stress_table,
    tail_risk_table,
)
from quant_toolbox.risk import (
    NO_ADV,
    SCENARIOS,
    correlation_summary,
    euler_contributions,
    exposures,
    factor_risk,
    historical_stress,
    liquidity,
    volatility_contributions,
)


def _values(table: TableBlock) -> dict[str, object]:
    return {str(row[0]): row[1] for row in table.rows}


def test_exposure_table_shows_the_measures_of_the_book() -> None:
    table = exposure_table(exposures(np.array([0.5, -0.3, 0.2])))

    assert table.columns[0].name == "Measure" and table.columns[1].name == "Value"
    assert _values(table) == {
        "Gross": "100.00%",
        "Net": "40.00%",
        "Long": "70.00%",
        "Short": "-30.00%",
        "Positions": "3",
        "Largest position": "50.00%",
        "Top five positions": "100.00%",
        "Effective positions": f"{1 / 0.38:.2f}",
    }


def test_group_exposure_table_sums_each_group_and_names_the_ungrouped() -> None:
    table = group_exposure_table(["A", "B", "C"], [0.5, -0.3, 0.2], {"A": "Tech", "B": "Tech"})

    assert table.shade
    assert [row[0] for row in table.rows] == ["Tech", "Ungrouped"]
    tech, ungrouped = table.rows
    assert tech[1] == pytest.approx(0.2) and tech[2] == pytest.approx(0.8)
    assert ungrouped[1] == pytest.approx(0.2) and ungrouped[2] == pytest.approx(0.2)


def test_tail_risk_on_a_known_sample_and_against_the_performance_functions() -> None:
    returns = np.linspace(-0.10, 0.10, 101)  # the 5th percentile is -0.09
    table = tail_risk_table(returns)

    assert [column.name for column in table.columns] == ["Measure", "95%", "99%"]
    historical_var = table.rows[0]
    assert historical_var[1] == pytest.approx(-0.09, abs=1e-9)
    assert table.rows[1][1] == pytest.approx(-0.095, abs=1e-9)  # mean of the five at or below
    for row, measure in zip(
        table.rows,
        [value_at_risk, expected_shortfall, parametric_var, parametric_expected_shortfall],
        strict=True,
    ):
        assert row[1] == pytest.approx(measure(returns, 0.05))
        assert row[2] == pytest.approx(measure(returns, 0.01))
    assert "per bar" in table.note.lower() and "loss is negative" in table.note


def test_tail_risk_refuses_a_level_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        tail_risk_table(np.zeros(10), levels=(0.95, 1.5))
    with pytest.raises(ValueError, match="between 0 and 1"):
        tail_risk_table(np.zeros(10), levels=(0.0,))


def test_contribution_rows_are_sorted_and_the_components_sum_to_volatility() -> None:
    breakdown = euler_contributions(["A", "B"], [0.5, 0.5], np.diag([0.04, 0.09]))
    table = contribution_table(breakdown)

    # Component of B is 0.5 * 0.045 = 0.0225 and of A is 0.5 * 0.02 = 0.01 (volatility^2 0.0325).
    assert [row[0] for row in table.rows] == ["B", "A"]
    assert sum(row[4] for row in table.rows) == pytest.approx(breakdown.volatility)
    assert table.rows[0][5] == pytest.approx(0.0225 / 0.0325)
    assert "1.39" in table.note  # diversification 0.25 / sqrt(0.0325)
    assert "sum to volatility" in table.note


def test_contribution_table_trims_to_top_and_says_so() -> None:
    breakdown = euler_contributions(["A", "B"], [0.5, 0.5], np.diag([0.04, 0.09]))
    table = contribution_table(breakdown, top=1)

    assert [row[0] for row in table.rows] == ["B"]
    assert "top 1 of 2" in table.note
    with pytest.raises(ValueError):
        contribution_table(breakdown, top=0)


def test_contribution_table_shows_none_as_n_a_for_a_riskless_book() -> None:
    breakdown = euler_contributions(["A"], [0.5], np.zeros((1, 1)))
    table = contribution_table(breakdown)
    document = Document("Risk", blocks=[table])

    assert table.rows[0][4] is None and table.rows[0][5] is None
    assert "diversification ratio n/a" in table.note
    assert "| A | 50.00% | 0.00% | n/a | n/a | n/a |" in render_markdown(document)


def test_stress_table_shows_an_uncovered_window_as_n_a() -> None:
    table = stress_table({"Covered": -0.1, "Not covered": None})
    text = render_markdown(Document("Risk", blocks=[table]))

    assert table.shade
    assert "| Not covered | n/a |" in text
    assert "| Covered | -10.00% |" in text
    assert "does not cover" in table.note


def test_liquidity_table_lines_the_cumulative_shares_up_with_the_buckets() -> None:
    result = liquidity(
        {"A": 1000.0, "B": -50.0, "C": 150.0}, {"A": 500.0, "B": 250.0, "C": 0.0}, 0.20
    )
    table = liquidity_table(result)

    labels = [row[0] for row in table.rows]
    assert labels[0] == "1 day or less" and labels[-1] == NO_ADV
    assert len(table.rows) == 8  # seven buckets and the no-ADV row
    assert table.rows[0][1] == pytest.approx(150.0 / 1200.0)
    assert table.rows[1][2] == pytest.approx(750.0 / 1200.0)  # within 7 days
    assert table.rows[2][2] == pytest.approx(1050.0 / 1200.0)  # within 30 days
    assert table.rows[-2][2] == pytest.approx(1050.0 / 1200.0)  # all timed, nothing past 30 days
    assert table.rows[-1][1] == pytest.approx(150.0 / 1200.0)
    assert table.rows[-1][2] is None
    assert "20.00%" in table.note and "C" in table.note


def test_factor_table_on_a_known_one_factor_model() -> None:
    result = factor_risk(
        {"A": 0.5, "B": 0.5},
        {"A": {"F": 1.0}, "B": {"F": 0.5}},
        {"F": {"F": 0.04}},
        {"A": 0.01, "B": 0.01},
    )
    table = factor_table(result)

    # Exposure 0.75; factor variance 0.75^2 * 0.04 = 0.0225; specific 0.005; total 0.0275.
    factor, specific = table.rows
    assert factor[1] == pytest.approx(0.75)
    assert factor[2] == pytest.approx(0.0225 / 0.0275)
    assert specific[0] == "Specific" and specific[1] is None
    assert specific[2] == pytest.approx(0.005 / 0.0275)
    assert factor[3] + specific[3] == pytest.approx(result.volatility)
    assert math.isclose(result.volatility, math.sqrt(0.0275))


def test_correlation_table_has_unit_diagonal_and_names_the_extreme_pairs() -> None:
    summary = correlation_summary(
        {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [2.0, 4.0, 6.0, 8.0, 10.0],
            "C": [5.0, 4.0, 3.0, 2.0, 1.0],
        }
    )
    table = correlation_table(summary)

    assert [column.name for column in table.columns] == ["Name", "A", "B", "C"]
    for index, row in enumerate(table.rows):
        assert row[index + 1] == pytest.approx(1.0)
    assert table.shade
    assert "Highest A and B (1.00)" in table.note
    assert "lowest" in table.note and "(-1.00)" in table.note
    assert "5 bars" in table.note


def _book() -> tuple[list[dt.date], dict[str, np.ndarray], dict[str, float], dict[str, str]]:
    positions, prices, groups = demo_book(count=300)
    index = {name: column for column, name in enumerate(prices.columns)}
    returns = prices.values[1:] / prices.values[:-1] - 1.0
    asset_returns = {name: returns[:, index[name]] for name in positions.columns}
    weights = {
        name: float(positions.values[-1, column]) for column, name in enumerate(positions.columns)
    }
    return prices.dates[1:], asset_returns, weights, groups


def _factor_inputs(
    weights: dict[str, float], asset_returns: dict[str, np.ndarray]
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], dict[str, float]]:
    market = np.mean(np.column_stack(list(asset_returns.values())), axis=1)
    variance = float(np.var(market, ddof=1)) * 252
    return (
        {name: {"Market": 1.0} for name in weights},
        {"Market": {"Market": variance}},
        {name: 0.01 for name in weights},
    )


def test_risk_report_lays_out_every_section_in_order_on_the_demo_book() -> None:
    dates, asset_returns, weights, groups = _book()
    loadings, covariance, specific = _factor_inputs(weights, asset_returns)
    document = risk_report(
        weights=weights,
        dates=dates,
        asset_returns=asset_returns,
        periods_per_year=252,
        groups=groups,
        adv={name: 5e6 for name in weights},
        positions={name: 1e6 * weight for name, weight in weights.items()},
        factor_exposures=loadings,
        factor_covariance=covariance,
        specific_variance=specific,
        top=5,
        title="Book",
        subtitle="Demo book",
        disclaimer="For internal use.",
    )

    assert [block.title for block in document.blocks] == [
        "Exposure",
        "Exposure by group",
        "Value at risk and expected shortfall",
        "Contribution to risk",
        "Historical stress",
        "Liquidity",
        "Factor risk",
        "Correlation",
    ]
    assert document.title == "Book" and document.subtitle == "Demo book"
    assert document.footer == "For internal use."

    held = list(weights)
    book = np.nan_to_num(np.column_stack([asset_returns[name] for name in held])) @ np.array(
        [weights[name] for name in held]
    )
    tail = document.blocks[2]
    assert isinstance(tail, TableBlock)
    assert tail.rows[0][1] == pytest.approx(value_at_risk(book, 0.05))
    contribution = document.blocks[3]
    assert isinstance(contribution, TableBlock)
    assert len(contribution.rows) == 5  # trimmed to top
    stress = document.blocks[4]
    assert isinstance(stress, TableBlock)
    assert stress.rows[0][1] is None  # 2007 is outside the demo dates
    assert [str(row[0]) for row in stress.rows] == list(SCENARIOS)

    html = render_html(document)
    assert "Factor risk" in html and "For internal use." in html
    assert "| Specific |" in render_markdown(document)


def test_sections_appear_only_when_their_inputs_are_given() -> None:
    dates, asset_returns, weights, _ = _book()
    document = risk_report(
        weights=weights, dates=dates, asset_returns=asset_returns, periods_per_year=252
    )

    assert [block.title for block in document.blocks] == [
        "Exposure",
        "Value at risk and expected shortfall",
        "Contribution to risk",
        "Historical stress",
        "Correlation",
    ]
    assert document.footer == ""


def test_risk_report_refuses_partial_inputs_and_bad_returns() -> None:
    dates, asset_returns, weights, _ = _book()
    loadings, _, _ = _factor_inputs(weights, asset_returns)
    common = {"dates": dates, "periods_per_year": 252}

    with pytest.raises(ValueError, match="adv and positions"):
        risk_report(weights=weights, asset_returns=asset_returns, adv={"ASSET00": 1.0}, **common)
    with pytest.raises(ValueError, match="together"):
        risk_report(
            weights=weights, asset_returns=asset_returns, factor_exposures=loadings, **common
        )
    with pytest.raises(ValueError, match="No returns for ASSET03"):
        missing = {name: series for name, series in asset_returns.items() if name != "ASSET03"}
        risk_report(weights=weights, asset_returns=missing, **common)
    short = dict(asset_returns, ASSET01=asset_returns["ASSET01"][:-1])
    with pytest.raises(ValueError, match="ASSET01 has"):
        risk_report(weights=weights, asset_returns=short, **common)


def test_risk_report_renders_as_html_markdown_and_pdf() -> None:
    pytest.importorskip("fpdf")
    from quant_toolbox.report.pdf import render_pdf

    dates, asset_returns, weights, groups = _book()
    document = risk_report(
        weights=weights,
        dates=dates,
        asset_returns=asset_returns,
        periods_per_year=252,
        groups=groups,
    )

    assert "<table>" in render_html(document)
    assert "## Exposure by group" in render_markdown(document)
    data = render_pdf(document)
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")


def test_risk_report_matches_the_risk_functions_it_lays_out() -> None:
    dates, asset_returns, weights, _ = _book()
    document = risk_report(
        weights=weights, dates=dates, asset_returns=asset_returns, periods_per_year=252
    )
    held = list(weights)
    vector = np.array([weights[name] for name in held])
    matrix = np.column_stack([asset_returns[name] for name in held])
    breakdown = volatility_contributions(
        weights, {name: np.nan_to_num(asset_returns[name]) for name in held}, 252
    )
    contribution = document.blocks[2]
    assert isinstance(contribution, TableBlock)
    assert contribution.note.startswith(f"Total volatility {breakdown.volatility:.2%}")
    stress = document.blocks[3]
    assert isinstance(stress, TableBlock)
    expected = historical_stress(vector, dates, matrix)
    assert {str(row[0]): row[1] for row in stress.rows} == expected
