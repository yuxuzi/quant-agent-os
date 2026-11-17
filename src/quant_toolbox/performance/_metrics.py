"""Performance metrics with explicit conventions, from either backend.

Conventions used by every function here:
- Returns are simple per-bar returns, ``r_t = p_t / p_{t-1} - 1``, as fractions (0.01 is 1%).
  Missing values (NaN) are dropped, one series at a time; a benchmark comparison uses the bars
  where both are present.
- Annualisation uses ``periods_per_year`` bars: 252 daily, 52 weekly, 12 monthly, or any count.
- Annualised return is geometric: ``prod(1 + r) ** (periods_per_year / n) - 1``.
- Volatility is the sample standard deviation (``ddof=1``) times ``sqrt(periods_per_year)``.
- Sharpe is the mean excess return over its sample standard deviation, times
  ``sqrt(periods_per_year)``. The annual risk-free rate becomes a per-bar rate by simple
  division. Sortino divides by downside deviation instead, ``sqrt(mean(min(r - required, 0) ** 2))``
  annualised, against a per-bar required return.
- Maximum drawdown is the most negative peak-to-trough change of compounded wealth, as a
  negative fraction (-0.25 is a 25% drawdown). Calmar is annualised return over its size.
- Omega is probability-weighted gains over losses around a threshold (zero by default); the tail
  ratio is the 95th percentile over the absolute 5th; stability is the R squared of a linear fit
  to cumulative log returns.
- Value at risk is historical: the ``cutoff`` percentile of per-bar returns, with linear
  interpolation, so a negative return (-0.02 is a 2% loss).
- Beta is the OLS slope on the benchmark; alpha is the per-bar intercept of excess returns,
  compounded: ``(1 + alpha) ** periods_per_year - 1``. Active return is the annualised arithmetic
  mean of returns minus benchmark, tracking error its annualised volatility, and the information
  ratio the first over the second.
- An undefined metric is NaN: a ratio of a series that does not vary, Sortino with no downside,
  Calmar with no drawdown, or too few values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from quant_toolbox._types import BoolArray, FloatArray
from quant_toolbox.performance import _numpy
from quant_toolbox.performance._backend import implementation

Series = FloatArray | Sequence[float]
METRIC_NAMES = (
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "omega_ratio",
    "max_drawdown",
    "tail_ratio",
    "stability",
    "value_at_risk",
    "hit_rate",
)


def simple_returns(prices: FloatArray) -> FloatArray:
    """Return per-bar simple returns along the first axis (one row fewer than ``prices``)."""
    values = np.asarray(prices, dtype=np.float64)
    result: FloatArray = values[1:] / values[:-1] - 1.0
    return result


def bar_returns(prices: FloatArray, delisting_return: float = 0.0) -> tuple[FloatArray, BoolArray]:
    """Return ``(returns, exits)``, both bars by assets, with no NaN in ``returns``.

    Asset returns never look through a missing price: ``returns[t]`` is the return over bar t
    (row 0 is 0); inside a gap it is 0, and on the bar prices resume it is the price over the
    last known price, minus 1. An asset whose prices stop before the panel ends is delisted:
    ``exits[t]`` is True on its first missing bar, where ``returns[t]`` is ``delisting_return``,
    and later bars return 0.
    """
    values = np.asarray(prices, dtype=np.float64)
    valid = np.isfinite(values)
    rows = np.arange(values.shape[0])[:, None]
    last_seen = np.maximum.accumulate(np.where(valid, rows, -1), axis=0)
    returns = np.zeros(values.shape)
    exits = np.zeros(values.shape, dtype=bool)
    if values.shape[0] < 2:
        return returns, exits
    previous = last_seen[:-1]
    priced = valid[1:] & (previous >= 0)
    known = np.take_along_axis(values, np.maximum(previous, 0), axis=0)
    returns[1:][priced] = values[1:][priced] / known[priced] - 1.0
    final = last_seen[-1]
    delisted = (final >= 0) & (final < values.shape[0] - 1)
    for asset in np.nonzero(delisted)[0]:
        returns[final[asset] + 1, asset] = delisting_return
        exits[final[asset] + 1, asset] = True
    return returns, exits


def annualized_return(
    returns: Series, periods_per_year: int, *, backend: str | None = None
) -> float:
    """Geometric annualised return."""
    return float(
        implementation(backend).annualized_return(_numpy.finite(returns), periods_per_year)
    )


def annualized_volatility(
    returns: Series, periods_per_year: int, *, backend: str | None = None
) -> float:
    """Sample standard deviation of returns, annualised."""
    values = _numpy.finite(returns)
    return float(implementation(backend).annualized_volatility(values, periods_per_year))


def sharpe_ratio(
    returns: Series, periods_per_year: int, risk_free: float = 0.0, *, backend: str | None = None
) -> float:
    """Annualised Sharpe ratio; ``risk_free`` is an annual rate. NaN when returns do not vary."""
    values = _numpy.finite(returns)
    if not _numpy.varies(values - risk_free / periods_per_year):
        return math.nan
    return float(implementation(backend).sharpe_ratio(values, periods_per_year, risk_free))


def sortino_ratio(
    returns: Series, periods_per_year: int, required: float = 0.0, *, backend: str | None = None
) -> float:
    """Annualised Sortino ratio against a per-bar ``required`` return; NaN with no downside."""
    values = _numpy.finite(returns)
    return float(implementation(backend).sortino_ratio(values, periods_per_year, required))


def calmar_ratio(returns: Series, periods_per_year: int, *, backend: str | None = None) -> float:
    """Annualised return over the size of the maximum drawdown; NaN with no drawdown."""
    return float(implementation(backend).calmar_ratio(_numpy.finite(returns), periods_per_year))


def omega_ratio(
    returns: Series,
    periods_per_year: int,
    risk_free: float = 0.0,
    required: float = 0.0,
    *,
    backend: str | None = None,
) -> float:
    """Gains over losses around a threshold: ``required`` is annual, ``risk_free`` per bar."""
    values = _numpy.finite(returns)
    return float(implementation(backend).omega_ratio(values, periods_per_year, risk_free, required))


def max_drawdown(returns: Series, *, backend: str | None = None) -> float:
    """Most negative peak-to-trough change of compounded wealth (0.0 if it never falls)."""
    return float(implementation(backend).max_drawdown(_numpy.finite(returns)))


def tail_ratio(returns: Series, *, backend: str | None = None) -> float:
    """The 95th percentile return over the absolute 5th percentile return."""
    return float(implementation(backend).tail_ratio(_numpy.finite(returns)))


def stability(returns: Series, *, backend: str | None = None) -> float:
    """R squared of a linear fit to cumulative log returns."""
    return float(implementation(backend).stability(_numpy.finite(returns)))


def value_at_risk(returns: Series, cutoff: float = 0.05, *, backend: str | None = None) -> float:
    """Historical value at risk per bar: the ``cutoff`` percentile of returns; NaN without data."""
    return float(implementation(backend).value_at_risk(_numpy.finite(returns), cutoff))


def expected_shortfall(returns: Series, cutoff: float = 0.05) -> float:
    """Mean return at or below `value_at_risk`; NaN without data."""
    values = _numpy.finite(returns)
    if values.size == 0:
        return math.nan
    threshold = _numpy.value_at_risk(values, cutoff)
    return float(np.mean(values[values <= threshold]))


def hit_rate(returns: Series) -> float:
    """Share of bars with a positive return."""
    values = _numpy.finite(returns)
    return float(np.mean(values > 0.0)) if values.size else math.nan


def turnover(weights: FloatArray) -> float:
    """Average one-way turnover per bar of a weights matrix (bars by assets).

    One-way turnover is half the sum of absolute weight changes in a bar.
    """
    values = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    if values.shape[0] < 2:
        return 0.0
    changes = np.abs(np.diff(values, axis=0)).sum(axis=1) / 2.0
    return float(np.mean(changes))


def beta(returns: Series, market: Series, *, backend: str | None = None) -> float:
    """OLS slope of ``returns`` on ``market`` over bars where both are finite."""
    y, x = _pairs(returns, market)
    if not _numpy.varies(x):
        return math.nan
    return float(implementation(backend).beta(y, x))


def alpha(
    returns: Series,
    market: Series,
    periods_per_year: int,
    risk_free: float = 0.0,
    *,
    backend: str | None = None,
) -> float:
    """Per-bar intercept of excess returns on excess market returns, compounded over a year."""
    y, x = _pairs(returns, market)
    if not _numpy.varies(x):
        return math.nan
    return float(implementation(backend).alpha(y, x, periods_per_year, risk_free))


def active_metrics(
    returns: Series, benchmark: Series, periods_per_year: int, *, backend: str | None = None
) -> dict[str, float]:
    """Annualised active return, tracking error, and information ratio against a benchmark."""
    left, right = _pairs(returns, benchmark)
    active = left - right
    if active.size < 2:
        return dict.fromkeys(("active_return", "tracking_error", "information_ratio"), math.nan)
    mean = float(np.mean(active)) * periods_per_year
    error = annualized_volatility(active, periods_per_year, backend=backend)
    ratio = mean / error if _numpy.varies(active) else math.nan
    return {"active_return": mean, "tracking_error": error, "information_ratio": ratio}


def benchmark_metrics(
    returns: Series,
    benchmark: Series,
    periods_per_year: int,
    risk_free: float = 0.0,
    *,
    backend: str | None = None,
) -> dict[str, float]:
    """Alpha, beta, active return, tracking error, and information ratio against a benchmark."""
    return {
        "alpha": alpha(returns, benchmark, periods_per_year, risk_free, backend=backend),
        "beta": beta(returns, benchmark, backend=backend),
        **active_metrics(returns, benchmark, periods_per_year, backend=backend),
    }


def summarize(
    returns: Series, periods_per_year: int, *, backend: str | None = None
) -> dict[str, float]:
    """The headline metrics of a return series."""
    return {
        "annualized_return": annualized_return(returns, periods_per_year, backend=backend),
        "annualized_volatility": annualized_volatility(returns, periods_per_year, backend=backend),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year, backend=backend),
        "max_drawdown": max_drawdown(returns, backend=backend),
        "hit_rate": hit_rate(returns),
    }


def all_metrics(
    returns: Series,
    periods_per_year: int,
    risk_free: float = 0.0,
    *,
    backend: str | None = None,
) -> dict[str, float]:
    """Every metric in `METRIC_NAMES`, with the risk-free rate in Sharpe."""
    return {
        "annualized_return": annualized_return(returns, periods_per_year, backend=backend),
        "annualized_volatility": annualized_volatility(returns, periods_per_year, backend=backend),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year, risk_free, backend=backend),
        "sortino_ratio": sortino_ratio(returns, periods_per_year, backend=backend),
        "calmar_ratio": calmar_ratio(returns, periods_per_year, backend=backend),
        "omega_ratio": omega_ratio(returns, periods_per_year, backend=backend),
        "max_drawdown": max_drawdown(returns, backend=backend),
        "tail_ratio": tail_ratio(returns, backend=backend),
        "stability": stability(returns, backend=backend),
        "value_at_risk": value_at_risk(returns, backend=backend),
        "hit_rate": hit_rate(returns),
    }


def _pairs(left: Series, right: Series) -> tuple[FloatArray, FloatArray]:
    x = np.asarray(left, dtype=np.float64).ravel()
    y = np.asarray(right, dtype=np.float64).ravel()
    if x.shape != y.shape:
        raise ValueError("The two series need one value per bar each.")
    both = np.isfinite(x) & np.isfinite(y)
    return x[both], y[both]
