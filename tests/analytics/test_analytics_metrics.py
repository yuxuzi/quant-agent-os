"""Known answers: empyrical's formulas are what the documented conventions say they are.

If an empyrical upgrade changed a convention (for example the standard deviation's degrees of
freedom), these tests fail before any number reaches a user.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quant_analytics import metrics

RETURNS = [0.02, -0.01, 0.03, -0.02, 0.01, 0.0]
MONTHLY = 12


def test_annualized_return_is_geometric() -> None:
    expected = math.prod(1 + r for r in RETURNS) ** (MONTHLY / len(RETURNS)) - 1

    assert metrics.annualized_return(RETURNS, MONTHLY) == pytest.approx(expected)
    assert metrics.annualized_return(RETURNS, 252) != pytest.approx(expected)  # any frequency


def test_volatility_and_sharpe_use_the_sample_deviation() -> None:
    deviation = float(np.std(RETURNS, ddof=1))
    mean = float(np.mean(RETURNS))

    assert metrics.annualized_volatility(RETURNS, MONTHLY) == pytest.approx(
        deviation * math.sqrt(MONTHLY)
    )
    assert metrics.sharpe_ratio(RETURNS, MONTHLY) == pytest.approx(
        mean / deviation * math.sqrt(MONTHLY)
    )
    # An annual risk-free rate becomes a per-period rate by simple division.
    assert metrics.sharpe_ratio(RETURNS, MONTHLY, risk_free=0.06) == pytest.approx(
        (mean - 0.005) / deviation * math.sqrt(MONTHLY)
    )


def test_downside_and_drawdown_ratios() -> None:
    values = np.array(RETURNS)
    downside = math.sqrt(float(np.mean(np.minimum(values, 0.0) ** 2))) * math.sqrt(MONTHLY)
    annual = metrics.annualized_return(RETURNS, MONTHLY)
    drawdown = metrics.max_drawdown(RETURNS)
    assert annual is not None and drawdown is not None

    assert metrics.sortino_ratio(RETURNS, MONTHLY) == pytest.approx(
        values.mean() * MONTHLY / downside
    )
    assert metrics.calmar_ratio(RETURNS, MONTHLY) == pytest.approx(annual / abs(drawdown))
    assert metrics.omega_ratio(RETURNS, MONTHLY) == pytest.approx(0.06 / 0.03)  # gains / losses


def test_max_drawdown_of_a_known_path() -> None:
    # Wealth 1.1, 0.55, 0.66: the drop from 1.1 to 0.55 is -50%.
    assert metrics.max_drawdown([0.10, -0.50, 0.20]) == pytest.approx(-0.5)
    assert metrics.max_drawdown([0.01, 0.02]) == 0.0


def test_tail_metrics_and_hit_rate() -> None:
    values = np.array(RETURNS)

    assert metrics.value_at_risk(RETURNS) == pytest.approx(np.percentile(values, 5))
    assert metrics.tail_ratio(RETURNS) == pytest.approx(
        abs(np.percentile(values, 95)) / abs(np.percentile(values, 5))
    )
    assert metrics.hit_rate(RETURNS) == pytest.approx(3 / 6)


def test_benchmark_metrics_of_a_known_relation() -> None:
    market = [0.01, -0.02, 0.03, 0.0, -0.01, 0.02]
    fund = [1.5 * m + 0.001 for m in market]
    active = np.array(fund) - np.array(market)

    result = metrics.benchmark_metrics(fund, market, MONTHLY)

    assert result["beta"] == pytest.approx(1.5)
    assert result["alpha"] == pytest.approx(1.001**MONTHLY - 1)  # compounded per-period alpha
    assert result["active_return"] == pytest.approx(active.mean() * MONTHLY)
    error = float(np.std(active, ddof=1)) * math.sqrt(MONTHLY)
    assert result["tracking_error"] == pytest.approx(error)
    assert result["information_ratio"] == pytest.approx(active.mean() * MONTHLY / error)


def test_undefined_metrics_are_none_not_infinite() -> None:
    flat = [0.01] * 6

    assert metrics.sharpe_ratio(flat, MONTHLY) is None
    assert metrics.annualized_volatility(flat, MONTHLY) == 0.0
    assert metrics.benchmark_metrics(flat, flat, MONTHLY)["information_ratio"] is None
