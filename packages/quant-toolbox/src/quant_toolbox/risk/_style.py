"""Returns-based style regression: a series' alpha and factor betas, with HAC t-statistics.

Conventions:
- OLS of per-bar returns on per-bar factor returns, with an intercept, over the bars where the
  series and every factor are finite. Returns are simple, as fractions; factors are whatever the
  caller supplies (excess market, size, value, momentum, a strategy index), aligned by bar.
- Alpha is the per-bar intercept times ``periods_per_year``: arithmetic, not compounded.
- t-statistics use Newey-West (HAC) standard errors with a Bartlett kernel, ``lags`` by default
  ``floor(4 * (n / 100) ** (2 / 9))``; ``lags=0`` gives White's robust errors.
- R squared is ``1 - SSR / SST``; adjusted R squared corrects for the ``p`` coefficients;
  residual volatility is ``sqrt(SSR / (n - p))`` times ``sqrt(periods_per_year)``.
- With no more observations than coefficients, every number is NaN. ``collinear`` means the
  factors are linearly dependent, so the betas are one of many solutions and should not be read
  one by one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from quant_toolbox._hac import newey_west_covariance, newey_west_lags
from quant_toolbox._types import FloatArray


@dataclass(frozen=True)
class StyleRegression:
    """A series regressed on factor returns: alpha, betas, their HAC t-statistics, and fit."""

    alpha: float
    alpha_t_stat: float
    betas: dict[str, float]
    t_stats: dict[str, float]
    r_squared: float
    adjusted_r_squared: float
    residual_volatility: float
    observations: int
    lags: int
    collinear: bool


def style_regression(
    returns: FloatArray | Sequence[float],
    factor_returns: Mapping[str, FloatArray | Sequence[float]],
    periods_per_year: int,
    lags: int | None = None,
) -> StyleRegression:
    """Regress ``returns`` on ``factor_returns`` with an intercept; see the module conventions."""
    names = list(factor_returns)
    if not names:
        raise ValueError("A style regression needs at least one factor.")
    y = np.asarray(returns, dtype=np.float64).ravel()
    columns = [np.asarray(factor_returns[name], dtype=np.float64).ravel() for name in names]
    if any(column.shape != y.shape for column in columns):
        raise ValueError("Every factor needs one value per bar of the returns.")
    factors = np.column_stack(columns)
    keep = np.isfinite(y) & np.isfinite(factors).all(axis=1)
    y, factors = y[keep], factors[keep]
    n, p = y.size, len(names) + 1
    count = newey_west_lags(n) if lags is None else max(0, lags)
    if n <= p:
        nan = dict.fromkeys(names, math.nan)
        return StyleRegression(
            math.nan, math.nan, nan, dict(nan), math.nan, math.nan, math.nan, n, count, False
        )
    design = np.column_stack([np.ones(n), factors])
    collinear = int(np.linalg.matrix_rank(design)) < p
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - design @ coefficients
    variance = np.diag(newey_west_covariance(design, residuals, count))
    t_stats = [
        float(c) / math.sqrt(v) if v > 0.0 else math.nan
        for c, v in zip(coefficients, variance, strict=True)
    ]
    ssr = float(residuals @ residuals)
    sst = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ssr / sst if sst > 0.0 else math.nan
    return StyleRegression(
        alpha=float(coefficients[0]) * periods_per_year,
        alpha_t_stat=t_stats[0],
        betas={name: float(coefficients[k + 1]) for k, name in enumerate(names)},
        t_stats={name: t_stats[k + 1] for k, name in enumerate(names)},
        r_squared=r_squared,
        adjusted_r_squared=1.0 - (1.0 - r_squared) * (n - 1) / (n - p),
        residual_volatility=math.sqrt(ssr / (n - p) * periods_per_year),
        observations=n,
        lags=count,
        collinear=collinear,
    )
