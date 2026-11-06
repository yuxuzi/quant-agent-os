"""Performance metrics from empyrical-reloaded, with their conventions stated (decision 0010).

The formulas are empyrical's, the library behind pyfolio; this module only adapts inputs and
outputs: lists in, plain floats out, and None where a metric is undefined (for example the
Sharpe ratio of a constant series). Conventions, checked by known-answer tests:
- Returns are simple per-period returns, as fractions (0.01 is 1%).
- ``periods_per_year`` annualises: 252 daily, 52 weekly, 12 monthly, or any other count.
- Annualised return is geometric: ``prod(1 + r) ** (periods_per_year / n) - 1``.
- Volatility is the sample standard deviation (``n - 1``) times ``sqrt(periods_per_year)``.
- Sharpe is the mean excess return over its sample standard deviation, times
  ``sqrt(periods_per_year)``; the annual risk-free rate becomes a per-period rate by simple
  division. Sortino divides by downside deviation instead (returns below zero).
- Maximum drawdown is the most negative peak-to-trough change of compounded wealth, as a
  negative fraction (-0.25 is a 25% drawdown). Calmar is annualised return over its size.
- Beta is the OLS slope on the benchmark; alpha is the per-period intercept, compounded:
  ``(1 + alpha) ** periods_per_year - 1``.
- Active return is the annualised arithmetic mean of returns minus benchmark returns, tracking
  error their annualised volatility, and the information ratio empyrical's excess Sharpe,
  annualised; these three are composed here from empyrical's functions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import empyrical
import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
FLAT = 1e-12  # relative deviation below which a series counts as constant


def annualized_return(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Geometric annualised return."""
    return _value(empyrical.annual_return(_array(returns), annualization=periods_per_year))


def annualized_volatility(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Sample standard deviation, annualised."""
    return _value(empyrical.annual_volatility(_array(returns), annualization=periods_per_year))


def sharpe_ratio(
    returns: Sequence[float], periods_per_year: int, risk_free: float = 0.0
) -> float | None:
    """Annualised Sharpe ratio; ``risk_free`` is an annual rate. None when returns do not vary."""
    series = _array(returns)
    if not _varies(series):
        return None
    return _value(
        empyrical.sharpe_ratio(
            series, risk_free=risk_free / periods_per_year, annualization=periods_per_year
        )
    )


def sortino_ratio(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Annualised Sortino ratio against a zero required return."""
    return _value(empyrical.sortino_ratio(_array(returns), annualization=periods_per_year))


def calmar_ratio(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Annualised return over the size of the maximum drawdown."""
    return _value(empyrical.calmar_ratio(_array(returns), annualization=periods_per_year))


def omega_ratio(returns: Sequence[float], periods_per_year: int) -> float | None:
    """Probability-weighted gains over losses, around a zero return."""
    return _value(empyrical.omega_ratio(_array(returns), annualization=periods_per_year))


def max_drawdown(returns: Sequence[float]) -> float | None:
    """Most negative peak-to-trough change of compounded wealth (0.0 if it never falls)."""
    return _value(empyrical.max_drawdown(_array(returns)))


def tail_ratio(returns: Sequence[float]) -> float | None:
    """The 95th percentile return over the absolute 5th percentile return."""
    return _value(empyrical.tail_ratio(_array(returns)))


def stability(returns: Sequence[float]) -> float | None:
    """R squared of a linear fit to cumulative log returns."""
    return _value(empyrical.stability_of_timeseries(_array(returns)))


def value_at_risk(returns: Sequence[float], cutoff: float = 0.05) -> float | None:
    """Historical value at risk: the ``cutoff`` percentile of per-period returns."""
    return _value(empyrical.value_at_risk(_array(returns), cutoff=cutoff))


def hit_rate(returns: Sequence[float]) -> float:
    """Share of periods with a positive return."""
    return float(np.mean(_array(returns) > 0.0))


def benchmark_metrics(
    returns: Sequence[float],
    benchmark: Sequence[float],
    periods_per_year: int,
    risk_free: float = 0.0,
) -> dict[str, float | None]:
    """Alpha, beta, active return, tracking error, and information ratio against a benchmark."""
    series, factor = _array(returns), _array(benchmark)
    alpha, beta = empyrical.alpha_beta(
        series, factor, risk_free=risk_free / periods_per_year, annualization=periods_per_year
    )
    active = series - factor
    excess = _value(empyrical.excess_sharpe(series, factor)) if _varies(active) else None
    return {
        "alpha": _value(alpha),
        "beta": _value(beta),
        "active_return": float(np.mean(active)) * periods_per_year,
        "tracking_error": _value(
            empyrical.annual_volatility(active, annualization=periods_per_year)
        ),
        "information_ratio": None if excess is None else excess * math.sqrt(periods_per_year),
    }


def _array(values: Sequence[float]) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _varies(series: FloatArray) -> bool:
    """Whether ``series`` varies by more than floating-point noise, which empyrical divides by."""
    deviation = float(np.nanstd(series, ddof=1)) if series.size > 1 else 0.0
    return deviation > FLAT * max(1.0, abs(float(np.nanmean(series))))


def _value(result: Any) -> float | None:
    number = float(result)
    return number if math.isfinite(number) else None
