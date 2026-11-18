"""Correlation among series, historical expected-shortfall contributions, and EWMA covariance.

Conventions:
- A correlation summary uses the bars where every series is finite (not pairwise-complete
  samples, which can give a matrix that is not a correlation matrix). A series that does not
  vary has NaN correlations. The average is over distinct pairs, ignoring NaN.
- Expected-shortfall contributions are historical and exact: the tail is the bars where today's
  book, ``asset_returns @ weights`` (a missing return counts as 0), is at or below its
  ``cutoff`` percentile (linear interpolation), the tail `expected_shortfall` uses; asset i's
  contribution is the mean of ``w_i r_i`` over those bars, so contributions sum to the book's
  expected shortfall.
- EWMA covariance (RiskMetrics) weights the bar k bars before the last by ``decay ** k``,
  normalised to sum to one, around a zero mean, and is annualised by ``periods_per_year``. A
  ``halflife`` in bars sets ``decay = 0.5 ** (1 / halflife)``. A missing return counts as 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from quant_toolbox._types import FloatArray

EWMA_DECAY = 0.94  # RiskMetrics' daily decay


@dataclass(frozen=True)
class CorrelationSummary:
    """A correlation matrix and its headline numbers."""

    names: list[str]
    matrix: FloatArray
    average: float
    highest: tuple[str, str, float] | None  # the most correlated pair
    lowest: tuple[str, str, float] | None  # the least correlated pair
    observations: int


def correlation_summary(
    series: Mapping[str, FloatArray | Sequence[float]],
) -> CorrelationSummary:
    """Correlations among two or more series, over the bars where all are finite."""
    names = list(series)
    if len(names) < 2:
        raise ValueError("A correlation summary needs at least two series.")
    data = np.column_stack([np.asarray(series[name], dtype=np.float64).ravel() for name in names])
    data = data[np.isfinite(data).all(axis=1)]
    count = len(names)
    matrix = np.full((count, count), np.nan)
    if data.shape[0] >= 3:
        centred = data - data.mean(axis=0)
        scale = np.sqrt(np.sum(centred**2, axis=0))
        varies = scale > 0.0
        safe = np.where(varies, scale, 1.0)
        matrix = (centred.T @ centred) / np.outer(safe, safe)
        matrix[~varies, :] = np.nan
        matrix[:, ~varies] = np.nan
        matrix = np.clip(matrix, -1.0, 1.0)
    pairs = [
        (names[i], names[j], float(matrix[i, j]))
        for i in range(count)
        for j in range(i + 1, count)
        if math.isfinite(matrix[i, j])
    ]
    return CorrelationSummary(
        names=names,
        matrix=matrix,
        average=float(np.mean([pair[2] for pair in pairs])) if pairs else math.nan,
        highest=max(pairs, key=lambda pair: pair[2]) if pairs else None,
        lowest=min(pairs, key=lambda pair: pair[2]) if pairs else None,
        observations=int(data.shape[0]),
    )


def expected_shortfall_contributions(
    weights: FloatArray, asset_returns: FloatArray, cutoff: float = 0.05
) -> FloatArray:
    """Each asset's share of today's book's historical expected shortfall; they sum to it.

    NaN for every asset when there are no bars.
    """
    held = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    returns = np.nan_to_num(_columns(asset_returns))
    if returns.shape[0] == 0:
        return np.full(held.shape, np.nan)
    book = returns @ held
    tail = book <= float(np.percentile(book, 100.0 * cutoff))
    result: FloatArray = (returns[tail] * held).mean(axis=0)
    return result


def ewma_covariance(
    asset_returns: FloatArray,
    periods_per_year: int,
    halflife: float | None = None,
    decay: float = EWMA_DECAY,
) -> FloatArray:
    """Exponentially weighted covariance of asset returns (bars by assets), annualised."""
    if halflife is not None:
        if halflife <= 0.0:
            raise ValueError(f"halflife is a positive number of bars; got {halflife}.")
        decay = 0.5 ** (1.0 / halflife)
    if not 0.0 < decay < 1.0:
        raise ValueError(f"decay must be between 0 and 1; got {decay}.")
    returns = np.nan_to_num(_columns(asset_returns))
    if returns.shape[0] == 0:
        raise ValueError("EWMA covariance needs at least one bar.")
    weights = decay ** np.arange(returns.shape[0] - 1, -1, -1, dtype=np.float64)
    weights /= weights.sum()
    result: FloatArray = (returns * weights[:, None]).T @ returns * periods_per_year
    return result


def _columns(asset_returns: FloatArray) -> FloatArray:
    """Bars by assets; a 1-d series is one asset's bars, as in `scenario_shocks`."""
    values = np.asarray(asset_returns, dtype=np.float64)
    result: FloatArray = values[:, None] if values.ndim == 1 else values
    return result
