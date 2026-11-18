"""Known answers for the measures added with Quant Toolbox 0.6.0.

Capture ratios, the shape of the distribution, Sharpe inference, the portfolio's parametric tail,
and concentration. Expected values come from the published formulas, written out here
independently of the toolbox.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

import pytest

from quant_agent_os.testing import check_tool
from quant_analytics.computed import (
    PerformanceInput,
    RiskInput,
    performance,
    portfolio_risk,
)
from quant_analytics.tools import analytics_tools

FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]
MARKET = [0.010, -0.002, 0.015, -0.012, 0.006, 0.012, -0.004, 0.009, 0.001, -0.007, 0.010, 0.004]
A = [0.02, -0.02] * 6
B = [0.01, 0.01, -0.01, -0.01] * 3


def _moments(values: list[float]) -> tuple[float, float, float, float]:
    """Mean, sample deviation, and population skew and kurtosis (not excess)."""
    n = len(values)
    mean = sum(values) / n
    deviations = [value - mean for value in values]
    m2 = sum(d**2 for d in deviations) / n
    m3 = sum(d**3 for d in deviations) / n
    m4 = sum(d**4 for d in deviations) / n
    return mean, math.sqrt(m2 * n / (n - 1)), m3 / m2**1.5, m4 / m2**2


def _adjusted(values: list[float]) -> tuple[float, float]:
    """Bias-adjusted skewness (G1) and excess kurtosis (G2)."""
    n = len(values)
    _, _, g1, b2 = _moments(values)
    skew = math.sqrt(n * (n - 1)) / (n - 2) * g1
    kurtosis = ((n + 1) * (b2 - 3.0) + 6.0) * (n - 1) / ((n - 2) * (n - 3))
    return skew, kurtosis


def test_a_fund_twice_its_benchmark_captures_twice_up_and_down() -> None:
    doubled = [2 * value for value in MARKET]
    output = performance(
        PerformanceInput(series_id="fund", returns=doubled, benchmark=MARKET, periods_per_year=12)
    )

    assert output.metrics["up_capture"] == pytest.approx(2.0)
    assert output.metrics["down_capture"] == pytest.approx(2.0)
    assert output.metrics["correlation"] == pytest.approx(1.0)
    assert "Up capture of fund is 2.00." in [item.statement for item in output.evidence]


def test_capture_ratios_need_a_benchmark() -> None:
    output = performance(PerformanceInput(series_id="fund", returns=FUND, periods_per_year=12))

    assert not {"up_capture", "down_capture", "correlation"} & set(output.metrics)


def test_the_distribution_matches_the_bias_adjusted_moments() -> None:
    output = performance(PerformanceInput(series_id="fund", returns=FUND, periods_per_year=12))
    skew, kurtosis = _adjusted(FUND)

    assert output.metrics["skewness"] == pytest.approx(skew, abs=1e-12)
    assert output.metrics["excess_kurtosis"] == pytest.approx(kurtosis, abs=1e-12)
    assert output.metrics["best_period"] == 0.021
    assert output.metrics["worst_period"] == -0.015
    statements = [item.statement for item in output.evidence]
    assert "Best period of fund is 2.10%." in statements


def test_sharpe_inference_follows_mertens_and_bailey_lopez_de_prado() -> None:
    output = performance(PerformanceInput(series_id="fund", returns=FUND, periods_per_year=12))
    n = len(FUND)
    mean, deviation, g3, g4 = _moments(FUND)
    sharpe = mean / deviation
    spread = 1 - g3 * sharpe + (g4 - 1) / 4 * sharpe**2
    per_bar_error = math.sqrt(spread / (n - 1))

    assert output.metrics["sharpe_standard_error"] == pytest.approx(
        per_bar_error * math.sqrt(12), rel=1e-12
    )
    assert output.metrics["probabilistic_sharpe_ratio"] == pytest.approx(
        NormalDist().cdf(sharpe / per_bar_error), rel=1e-9
    )
    assert output.metrics["minimum_track_record"] == pytest.approx(
        1 + spread * (NormalDist().inv_cdf(0.95) / sharpe) ** 2, rel=1e-9
    )
    assert set(output.conventions) >= set(output.metrics)


def test_a_losing_series_has_no_minimum_track_record() -> None:
    losing = [-value for value in FUND]
    output = performance(PerformanceInput(series_id="fund", returns=losing, periods_per_year=12))
    probability = output.metrics["probabilistic_sharpe_ratio"]

    assert output.metrics["minimum_track_record"] is None
    assert probability is not None and probability < 0.5


def test_the_portfolio_tail_is_cornish_fisher_and_gaussian() -> None:
    output = portfolio_risk(
        RiskInput(
            portfolio_id="book",
            weights={"a": 0.75, "b": 0.25},
            asset_returns={"a": FUND, "b": MARKET},
            periods_per_year=12,
        )
    )
    book = [0.75 * a + 0.25 * b for a, b in zip(FUND, MARKET, strict=True)]
    mean, deviation, _, _ = _moments(book)
    s, k = _adjusted(book)
    z = NormalDist().inv_cdf(0.05)
    z_cf = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
    density = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)

    assert output.parametric_var == pytest.approx(mean + z_cf * deviation, rel=1e-6)
    assert output.parametric_expected_shortfall == pytest.approx(
        mean - deviation * density / 0.05, rel=1e-6
    )
    assert output.herfindahl == pytest.approx(0.75**2 + 0.25**2)
    assert output.effective_positions == pytest.approx(1 / (0.75**2 + 0.25**2))


def test_concentration_counts_shorts_by_size() -> None:
    record = check_tool(
        analytics_tools().get("analyze_risk"),
        {
            "portfolio_id": "book",
            "weights": {"a": 0.5, "b": -0.5},
            "asset_returns": {"a": A, "b": B},
            "periods_per_year": 12,
        },
    )

    assert record.output is not None
    output: dict[str, Any] = record.output
    assert output["herfindahl"] == pytest.approx(0.5)
    assert output["effective_positions"] == pytest.approx(2.0)
    assert {"parametric_var", "parametric_expected_shortfall", "herfindahl"} <= set(
        output["conventions"]
    )
    statements = [item["statement"] for item in output["evidence"]]
    assert "book has 2.0 effective positions (Herfindahl 0.500)." in statements


def test_a_book_with_no_positions_has_no_concentration() -> None:
    output = portfolio_risk(
        RiskInput(
            portfolio_id="book", weights={"a": 0.0}, asset_returns={"a": A}, periods_per_year=12
        )
    )

    assert output.herfindahl is None and output.effective_positions is None
