"""The two backends answer the same: what makes "empyrical first, numpy otherwise" safe.

Each shared metric runs through both, on clean data and on the inputs where empyrical called
directly would differ (a constant series, no downside, two points, gaps), and must agree to
within 1e-12. Undefined is NaN in both.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from quant_toolbox import performance
from quant_toolbox.performance import BACKEND_VARIABLE, backend, backend_version, source

pytest.importorskip("empyrical")

RETURNS = np.random.default_rng(7).normal(0.0005, 0.01, 504)
BENCHMARK = 0.8 * RETURNS + np.random.default_rng(8).normal(0.0002, 0.006, 504)
GAPPY = np.where(np.arange(504) % 50 == 3, np.nan, RETURNS)
CASES = {
    "daily": RETURNS,
    "gappy": GAPPY,
    "constant": np.full(50, 0.01),
    "zero": np.zeros(50),
    "all positive": np.abs(RETURNS[:60]),
    "all negative": -np.abs(RETURNS[:60]),
    "two points": RETURNS[:2],
    "one point": RETURNS[:1],
    "empty": np.array([]),
}
Metric = Callable[..., float]
METRICS: dict[str, Callable[[np.ndarray, str], float]] = {
    "annualized_return": lambda r, b: performance.annualized_return(r, 252, backend=b),
    "annualized_volatility": lambda r, b: performance.annualized_volatility(r, 52, backend=b),
    "sharpe_ratio": lambda r, b: performance.sharpe_ratio(r, 252, backend=b),
    "sharpe_with_risk_free": lambda r, b: performance.sharpe_ratio(r, 12, 0.03, backend=b),
    "sortino_ratio": lambda r, b: performance.sortino_ratio(r, 252, backend=b),
    "sortino_required": lambda r, b: performance.sortino_ratio(r, 252, 0.0002, backend=b),
    "calmar_ratio": lambda r, b: performance.calmar_ratio(r, 252, backend=b),
    "omega_ratio": lambda r, b: performance.omega_ratio(r, 252, backend=b),
    "omega_required": lambda r, b: performance.omega_ratio(r, 252, 0.0, 0.05, backend=b),
    "max_drawdown": lambda r, b: performance.max_drawdown(r, backend=b),
    "tail_ratio": lambda r, b: performance.tail_ratio(r, backend=b),
    "stability": lambda r, b: performance.stability(r, backend=b),
    "value_at_risk": lambda r, b: performance.value_at_risk(r, backend=b),
    "value_at_risk_1": lambda r, b: performance.value_at_risk(r, 0.01, backend=b),
}


def _same(left: float, right: float) -> bool:
    if math.isnan(left) or math.isnan(right):
        return math.isnan(left) and math.isnan(right)
    return abs(left - right) <= 1e-12 * max(1.0, abs(left))


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("name", METRICS)
def test_both_backends_give_the_same_number(name: str, case: str) -> None:
    metric, values = METRICS[name], CASES[case]

    ours, theirs = metric(values, "numpy"), metric(values, "empyrical")

    assert _same(ours, theirs), f"{name} on {case}: numpy {ours!r}, empyrical {theirs!r}"


@pytest.mark.parametrize(
    "market",
    [BENCHMARK, np.where(np.arange(504) % 40 == 7, np.nan, BENCHMARK), np.full(504, 0.001)],
    ids=["clean", "gappy", "constant"],
)
@pytest.mark.parametrize("risk_free", [0.0, 0.04])
def test_benchmark_metrics_agree(market: np.ndarray, risk_free: float) -> None:
    ours = performance.benchmark_metrics(RETURNS, market, 252, risk_free, backend="numpy")
    theirs = performance.benchmark_metrics(RETURNS, market, 252, risk_free, backend="empyrical")

    for name in ours:
        assert _same(ours[name], theirs[name]), name


def test_the_variable_and_the_argument_choose_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BACKEND_VARIABLE, "numpy")
    assert backend() == "numpy"
    assert source().startswith("numpy")
    assert backend("empyrical") == "empyrical"
    assert backend_version("empyrical").startswith("empyrical-reloaded ")

    monkeypatch.setenv(BACKEND_VARIABLE, "auto")
    assert backend() == "empyrical"
    assert source() == "empyrical-reloaded (the metrics behind pyfolio)"


def test_an_unknown_backend_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BACKEND_VARIABLE, "pandas")

    with pytest.raises(ValueError, match="Unknown metrics backend 'pandas'"):
        performance.sharpe_ratio(RETURNS, 252)


def test_conventions_name_the_backend() -> None:
    conventions: dict[str, Any] = performance.conventions("numpy")

    assert conventions["source"].startswith("numpy")
    assert set(performance.METRIC_NAMES) <= set(conventions)
