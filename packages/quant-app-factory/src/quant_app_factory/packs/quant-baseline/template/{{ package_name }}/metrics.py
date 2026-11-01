"""Performance metrics with explicit conventions.

Conventions used by every function here:
- Returns are simple per-bar returns, ``r_t = p_t / p_{t-1} - 1``, as fractions (0.01 is 1%).
- Annualisation uses ``periods_per_year`` bars: 252 daily, 52 weekly, 12 monthly.
- Annualised return is geometric: ``prod(1 + r) ** (periods_per_year / n) - 1``.
- Volatility is the sample standard deviation (``ddof=1``) times ``sqrt(periods_per_year)``.
- Sharpe is the mean excess return over its sample standard deviation, times
  ``sqrt(periods_per_year)``. The annual risk-free rate becomes a per-bar rate by simple
  division. Sharpe is NaN when returns do not vary (up to floating-point noise).
- Maximum drawdown is the most negative peak-to-trough change of compounded wealth, as a
  negative fraction (-0.25 is a 25% drawdown).
- Turnover is one-way: half the sum of absolute weight changes in a bar, averaged over bars.
- Information coefficients compare a signal at bar t with the forward return that starts at t.
  NaN pairs are ignored, and bars with fewer than three valid pairs are NaN.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
MIN_PAIRS = 3
FLAT = 1e-12  # relative deviation below which returns count as constant


def simple_returns(prices: FloatArray) -> FloatArray:
    """Return per-bar simple returns along the first axis (one row fewer than ``prices``)."""
    values = np.asarray(prices, dtype=np.float64)
    return values[1:] / values[:-1] - 1.0


def annualized_return(returns: FloatArray, periods_per_year: int) -> float:
    """Geometric annualised return of a return series."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    growth = float(np.prod(1.0 + values))
    return float(growth ** (periods_per_year / values.size)) - 1.0


def annualized_volatility(returns: FloatArray, periods_per_year: int) -> float:
    """Sample standard deviation of returns, annualised."""
    values = _finite(returns)
    if values.size < 2:
        return math.nan
    return float(np.std(values, ddof=1)) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: FloatArray, periods_per_year: int, risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio; NaN when the returns do not vary."""
    values = _finite(returns)
    if values.size < 2:
        return math.nan
    excess = values - risk_free / periods_per_year
    deviation = float(np.std(excess, ddof=1))
    if deviation <= FLAT * max(1.0, abs(float(np.mean(excess)))):
        return math.nan
    return float(np.mean(excess)) / deviation * math.sqrt(periods_per_year)


def max_drawdown(returns: FloatArray) -> float:
    """Most negative peak-to-trough change of compounded wealth (0.0 if it never falls)."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    wealth = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    peaks = np.maximum.accumulate(wealth)
    return float(np.min(wealth / peaks - 1.0))


def hit_rate(returns: FloatArray) -> float:
    """Share of bars with a positive return."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    return float(np.mean(values > 0.0))


def turnover(weights: FloatArray) -> float:
    """Average one-way turnover per bar of a weights matrix (bars by assets)."""
    values = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    if values.shape[0] < 2:
        return 0.0
    changes = np.abs(np.diff(values, axis=0)).sum(axis=1) / 2.0
    return float(np.mean(changes))


def ranks(values: FloatArray) -> FloatArray:
    """Rank a 1-d array from 1, giving tied values their average rank."""
    data = np.asarray(values, dtype=np.float64)
    sorter = np.argsort(data, kind="mergesort")
    inverse = np.empty(sorter.size, dtype=np.intp)
    inverse[sorter] = np.arange(sorter.size)
    ordered = data[sorter]
    starts = np.concatenate(([True], ordered[1:] != ordered[:-1]))
    dense = np.cumsum(starts)[inverse]
    bounds = np.concatenate((np.nonzero(starts)[0], [starts.size]))
    average: FloatArray = 0.5 * (bounds[dense] + bounds[dense - 1] + 1).astype(np.float64)
    return average


def information_coefficient(
    signal: FloatArray, forward_returns: FloatArray, *, rank: bool = False
) -> FloatArray:
    """Per-bar correlation between a signal and forward returns (both bars by assets).

    ``rank=True`` gives the rank (Spearman) coefficient; otherwise Pearson.
    """
    coefficients = np.full(signal.shape[0], np.nan)
    for bar in range(signal.shape[0]):
        valid = np.isfinite(signal[bar]) & np.isfinite(forward_returns[bar])
        if int(valid.sum()) < MIN_PAIRS:
            continue
        left, right = signal[bar][valid], forward_returns[bar][valid]
        if rank:
            left, right = ranks(left), ranks(right)
        if np.std(left) == 0.0 or np.std(right) == 0.0:
            continue
        coefficients[bar] = float(np.corrcoef(left, right)[0, 1])
    return coefficients


def summarize(returns: FloatArray, periods_per_year: int) -> dict[str, float]:
    """Return the headline metrics of a return series."""
    return {
        "annualized_return": annualized_return(returns, periods_per_year),
        "annualized_volatility": annualized_volatility(returns, periods_per_year),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "hit_rate": hit_rate(returns),
    }


def _finite(values: FloatArray) -> FloatArray:
    data = np.asarray(values, dtype=np.float64).ravel()
    return data[np.isfinite(data)]
