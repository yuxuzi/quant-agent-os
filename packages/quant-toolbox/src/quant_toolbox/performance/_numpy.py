"""The numpy backend: every metric written out, for when empyrical is not installed.

Each function drops missing values itself and answers NaN where a metric is undefined. The
formulas are empyrical's, and `tests/test_backends.py` holds them to it.
"""

from __future__ import annotations

import math

import numpy as np

from quant_toolbox._numeric import FLAT, finite, varies
from quant_toolbox._types import FloatArray

__all__ = ["FLAT", "finite", "varies"]


def annualized_return(values: FloatArray, periods_per_year: int) -> float:
    if values.size == 0:
        return math.nan
    growth = float(np.prod(1.0 + values))
    return float(growth ** (periods_per_year / values.size)) - 1.0


def annualized_volatility(values: FloatArray, periods_per_year: int) -> float:
    if values.size < 2:
        return math.nan
    return float(np.std(values, ddof=1)) * math.sqrt(periods_per_year)


def sharpe_ratio(values: FloatArray, periods_per_year: int, risk_free: float) -> float:
    excess = values - risk_free / periods_per_year
    return float(np.mean(excess)) / float(np.std(excess, ddof=1)) * math.sqrt(periods_per_year)


def sortino_ratio(values: FloatArray, periods_per_year: int, required: float) -> float:
    if values.size == 0:
        return math.nan
    excess = values - required
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))) * math.sqrt(periods_per_year)
    if downside == 0.0:
        return math.nan
    return float(np.mean(excess)) * periods_per_year / downside


def max_drawdown(values: FloatArray) -> float:
    if values.size == 0:
        return math.nan
    wealth = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    peaks = np.maximum.accumulate(wealth)
    return float(np.min(wealth / peaks - 1.0))


def calmar_ratio(values: FloatArray, periods_per_year: int) -> float:
    drawdown = max_drawdown(values)
    if math.isnan(drawdown) or drawdown >= 0.0:
        return math.nan
    return annualized_return(values, periods_per_year) / abs(drawdown)


def omega_ratio(
    values: FloatArray, periods_per_year: int, risk_free: float, required: float
) -> float:
    if values.size < 2 or required <= -1.0:
        return math.nan
    threshold = (
        required if periods_per_year == 1 else (1.0 + required) ** (1.0 / periods_per_year) - 1.0
    )
    above = values - risk_free - threshold
    gains = float(above[above > 0.0].sum())
    losses = -float(above[above < 0.0].sum())
    return gains / losses if losses > 0.0 else math.nan


def tail_ratio(values: FloatArray) -> float:
    if values.size == 0:
        return math.nan
    low = abs(float(np.percentile(values, 5)))
    return abs(float(np.percentile(values, 95))) / low if low > 0.0 else math.nan


def stability(values: FloatArray) -> float:
    if values.size < 2:
        return math.nan
    level = np.cumsum(np.log1p(values))
    steps = np.arange(level.size, dtype=np.float64)
    x, y = steps - steps.mean(), level - level.mean()
    spread_x, spread_y = float(x @ x), float(y @ y)
    if spread_x == 0.0 or spread_y == 0.0:
        return math.nan  # a flat path has no fit to measure
    r = max(-1.0, min(1.0, float(x @ y) / math.sqrt(spread_x * spread_y)))
    return r * r


def value_at_risk(values: FloatArray, cutoff: float) -> float:
    if values.size == 0:
        return math.nan
    return float(np.percentile(values, 100.0 * cutoff))


def beta(returns: FloatArray, market: FloatArray) -> float:
    center = float(np.mean(market))
    x = market - center
    y = returns - float(np.mean(returns))
    return float(x @ y) / float(x @ x)


def alpha(
    returns: FloatArray, market: FloatArray, periods_per_year: int, risk_free: float
) -> float:
    rate = risk_free / periods_per_year
    y, x = returns - rate, market - rate
    slope = beta(y, x)
    return (1.0 + float(np.mean(y - slope * x))) ** periods_per_year - 1.0
