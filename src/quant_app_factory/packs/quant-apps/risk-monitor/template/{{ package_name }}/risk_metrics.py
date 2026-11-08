"""Return-based risk measures, beyond `metrics`, with explicit conventions.

- Value at risk is historical: the ``cutoff`` percentile of per-bar returns, with linear
  interpolation, so it is a negative return (-0.02 means a 2% loss at that cutoff).
- Expected shortfall is the mean of the returns at or below value at risk.
- Sortino is the mean excess return, annualised arithmetically, over downside deviation:
  ``sqrt(mean(min(r, 0) ** 2)) * sqrt(periods_per_year)``.
- Calmar is the geometric annualised return over the size of the maximum drawdown.
"""

from __future__ import annotations

import math

import numpy as np

from .metrics import FloatArray, _finite, annualized_return, max_drawdown


def value_at_risk(returns: FloatArray, cutoff: float = 0.05) -> float:
    """Historical value at risk per bar; NaN without data."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    return float(np.percentile(values, 100.0 * cutoff))


def expected_shortfall(returns: FloatArray, cutoff: float = 0.05) -> float:
    """Mean return at or below `value_at_risk`; NaN without data."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    return float(np.mean(values[values <= value_at_risk(values, cutoff)]))


def sortino_ratio(returns: FloatArray, periods_per_year: int, required: float = 0.0) -> float:
    """Annualised Sortino ratio against a per-bar ``required`` return; NaN with no downside."""
    values = _finite(returns)
    if values.size == 0:
        return math.nan
    excess = values - required
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))) * math.sqrt(periods_per_year)
    if downside == 0.0:
        return math.nan
    return float(np.mean(excess)) * periods_per_year / downside


def calmar_ratio(returns: FloatArray, periods_per_year: int) -> float:
    """Annualised return over the size of the maximum drawdown; NaN with no drawdown."""
    drawdown = max_drawdown(returns)
    if math.isnan(drawdown) or drawdown == 0.0:
        return math.nan
    return annualized_return(returns, periods_per_year) / abs(drawdown)
