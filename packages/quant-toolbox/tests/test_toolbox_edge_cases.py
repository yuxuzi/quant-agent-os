"""Edge cases found in review: coarse data, excess returns, one-asset inputs, and bad arguments."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_toolbox import performance, report, research, risk
from quant_toolbox.data import business_days, random_walk_panel


def test_a_scenario_needs_daily_data_across_its_window() -> None:
    start, end = dt.date(2020, 2, 19), dt.date(2020, 3, 23)
    month_ends = [dt.date(2020, 1, 31), dt.date(2020, 2, 29), dt.date(2020, 3, 31)]
    monthly = np.array([[0.01], [-0.08], [-0.12]])

    assert risk.scenario_shocks(month_ends, monthly, start, end) is None
    stressed = risk.historical_stress(np.array([1.0]), month_ends, monthly)
    assert all(value is None for value in stressed.values())

    days = business_days(60, dt.date(2020, 2, 3))
    daily = np.full((60, 1), -0.01)
    shocks = risk.scenario_shocks(days, daily, start, end)
    inside = sum(start < day <= end for day in days)
    assert shocks is not None
    assert shocks[0] == pytest.approx(0.99**inside - 1.0)


def test_sharpe_confidence_is_of_the_same_excess_returns_as_the_headline_sharpe() -> None:
    days = business_days(756)
    returns = np.random.default_rng(4).normal(0.05 / 252, 0.01, 756)

    plain = performance.analyze(days, returns, 252)
    excess = performance.analyze(days, returns, 252, risk_free=0.05)

    shifted = returns - 0.05 / 252
    assert excess.sharpe_inference["probabilistic_sharpe_ratio"] == pytest.approx(
        performance.probabilistic_sharpe_ratio(shifted, 252)
    )
    assert (
        excess.sharpe_inference["probabilistic_sharpe_ratio"]
        < plain.sharpe_inference["probabilistic_sharpe_ratio"]
    )


def test_a_one_dimensional_series_is_one_asset() -> None:
    series = np.random.default_rng(5).normal(0.0, 0.01, 500)

    covariance = risk.ewma_covariance(series, 252)
    contributions = risk.expected_shortfall_contributions(np.array([1.0]), series)

    assert covariance.shape == (1, 1)
    assert contributions == pytest.approx([performance.expected_shortfall(series)])


def test_brinson_refuses_a_repeated_group() -> None:
    weights = {"Tech": 0.5, "Cash": 0.5}
    returns = {"Tech": 0.02, "Cash": 0.0}

    with pytest.raises(ValueError, match="repeated: Tech"):
        performance.brinson(["Tech", "Tech", "Cash"], weights, returns, weights, returns)


def test_style_regression_reports_the_lags_it_used() -> None:
    rng = np.random.default_rng(6)
    market = rng.normal(0.0, 0.01, 20)
    fund = 0.5 * market + rng.normal(0.0, 0.002, 20)

    capped = risk.style_regression(fund, {"market": market}, 252, lags=50)
    exact = risk.style_regression(fund, {"market": market}, 252, lags=19)

    assert capped.lags == 19
    assert capped.t_stats == exact.t_stats


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_per_asset_costs_must_be_finite(bad: float) -> None:
    prices, _ = random_walk_panel(50, 3, seed=1)
    weights = research.dollar_neutral_weights(research.momentum(prices, 10, 1))

    with pytest.raises(ValueError, match="asset column 1"):
        research.run_backtest(prices, weights, cost_bps=np.array([5.0, bad, 5.0]))


def test_a_tear_sheet_of_annual_returns_has_no_rolling_chart() -> None:
    years = [dt.date(year, 12, 31) for year in range(2015, 2025)]
    returns = np.linspace(-0.05, 0.15, 10)

    document = report.tear_sheet(years, returns, 1)

    assert "Rolling one-year Sharpe" not in [block.title for block in document.blocks]
