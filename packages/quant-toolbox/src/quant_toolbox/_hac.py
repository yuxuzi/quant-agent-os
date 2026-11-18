"""Newey-West (HAC) t-statistics, shared by research (mean ICs) and risk (factor regressions).

Standard errors use a Bartlett kernel, so serial correlation, including the overlap of multi-bar
forward returns, does not inflate a t-statistic. The default lag count is
``floor(4 * (n / 100) ** (2 / 9))`` (Newey and West, 1994).
"""

from __future__ import annotations

import math

import numpy as np

from quant_toolbox._numeric import FLAT, finite
from quant_toolbox._types import FloatArray


def newey_west_lags(n: int) -> int:
    """The Newey-West (1994) rule-of-thumb lag count for ``n`` observations."""
    return math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0)) if n > 0 else 0


def newey_west_t_stat(values: FloatArray, lags: int | None = None) -> float:
    """t-statistic of the mean of ``values`` with Newey-West (HAC) standard errors.

    ``lags`` defaults to `newey_west_lags`; ``lags=0`` gives the plain t-statistic with a
    population variance. NaN values are dropped; NaN when there is nothing to test.
    """
    data = finite(values)
    n = data.size
    if n < 2:
        return math.nan
    count = newey_west_lags(n) if lags is None else max(0, min(lags, n - 1))
    deviations = data - float(np.mean(data))
    variance = float(deviations @ deviations) / n
    for lag in range(1, count + 1):
        weight = 1.0 - lag / (count + 1.0)
        variance += 2.0 * weight * float(deviations[lag:] @ deviations[:-lag]) / n
    if variance <= FLAT * max(1.0, float(np.mean(data)) ** 2):
        return math.nan
    return float(np.mean(data)) / math.sqrt(variance / n)


def newey_west_covariance(design: FloatArray, residuals: FloatArray, lags: int) -> FloatArray:
    """Newey-West (HAC) covariance of OLS coefficients: ``(X'X)^-1 S (X'X)^-1``.

    ``S`` sums the outer products of the scores ``x_t e_t``, plus Bartlett-weighted
    autocovariances up to ``lags``: ``(1 - l / (lags + 1)) (G_l + G_l')``. ``lags=0`` is White's
    heteroskedasticity-robust (HC0) covariance. A singular ``X'X`` uses its pseudo-inverse.
    """
    x = np.asarray(design, dtype=np.float64)
    scores = x * np.asarray(residuals, dtype=np.float64)[:, None]
    meat = scores.T @ scores
    count = max(0, min(lags, x.shape[0] - 1))  # as in newey_west_t_stat
    for lag in range(1, count + 1):
        weight = 1.0 - lag / (count + 1.0)
        gamma = scores[lag:].T @ scores[:-lag]
        meat += weight * (gamma + gamma.T)
    bread = np.linalg.pinv(x.T @ x)
    result: FloatArray = bread @ meat @ bread
    return result
