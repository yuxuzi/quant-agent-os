"""The shape of a return distribution, and value at risk from its moments.

Conventions:
- Missing values are dropped. Returns are simple per-bar returns, as fractions.
- Skewness and excess kurtosis are the bias-adjusted sample estimates (``G1`` and ``G2``, as
  ``scipy.stats.skew(bias=False)`` and ``scipy.stats.kurtosis(bias=False)`` give): skewness needs
  three returns and excess kurtosis four, and both need returns that vary. A normal distribution
  has 0 for both.
- Average gain and loss are the means of the positive and of the negative returns (the loss is
  negative); the gain-loss ratio is the first over the size of the second, and the profit factor
  is the sum of gains over the size of the sum of losses. Zero returns count as neither.
- Parametric value at risk is per bar and negative for a loss, like the historical
  `value_at_risk`: ``mean + z * sd`` with ``z`` the normal ``cutoff`` quantile and ``sd`` the
  sample standard deviation. Cornish-Fisher replaces ``z`` by
  ``z + (z^2 - 1) S / 6 + (z^3 - 3 z) K / 24 - (2 z^3 - 5 z) S^2 / 36``, with S and K the skewness
  and excess kurtosis above, so negative skew and fat tails deepen it. Expected shortfall is
  Gaussian: ``mean - sd * phi(z) / cutoff``.
- Undefined is NaN.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from quant_toolbox._numeric import finite, varies
from quant_toolbox._types import FloatArray
from quant_toolbox.performance._inference import normal_ppf

Series = FloatArray | Sequence[float]
VAR_METHODS = ("gaussian", "cornish_fisher")


@dataclass(frozen=True)
class Distribution:
    """The shape of a return series: moments, extremes, and gains against losses."""

    observations: int
    skewness: float
    excess_kurtosis: float
    best: float
    worst: float
    average_gain: float
    average_loss: float
    gain_loss_ratio: float
    profit_factor: float
    positive_share: float


def return_distribution(returns: Series) -> Distribution:
    """Moments, best and worst bars, and gains against losses; NaN where undefined."""
    values = finite(returns)
    gains, losses = values[values > 0.0], values[values < 0.0]
    average_gain = float(gains.mean()) if gains.size else math.nan
    average_loss = float(losses.mean()) if losses.size else math.nan
    return Distribution(
        observations=int(values.size),
        skewness=skewness(values),
        excess_kurtosis=excess_kurtosis(values),
        best=float(np.max(values)) if values.size else math.nan,
        worst=float(np.min(values)) if values.size else math.nan,
        average_gain=average_gain,
        average_loss=average_loss,
        gain_loss_ratio=average_gain / abs(average_loss) if losses.size else math.nan,
        profit_factor=float(gains.sum()) / abs(float(losses.sum())) if losses.size else math.nan,
        positive_share=float(np.mean(values > 0.0)) if values.size else math.nan,
    )


def skewness(returns: Series) -> float:
    """Bias-adjusted sample skewness (G1); NaN below three returns or when they do not vary."""
    values = finite(returns)
    n = values.size
    if n < 3 or not varies(values):
        return math.nan
    deviations = values - values.mean()
    m2 = float(np.mean(deviations**2))
    g1 = float(np.mean(deviations**3)) / m2**1.5
    return float(math.sqrt(n * (n - 1)) / (n - 2) * g1)


def excess_kurtosis(returns: Series) -> float:
    """Bias-adjusted sample excess kurtosis (G2); NaN below four returns or when constant."""
    values = finite(returns)
    n = values.size
    if n < 4 or not varies(values):
        return math.nan
    deviations = values - values.mean()
    m2 = float(np.mean(deviations**2))
    g2 = float(np.mean(deviations**4)) / m2**2 - 3.0
    return float(((n + 1) * g2 + 6.0) * (n - 1) / ((n - 2) * (n - 3)))


def parametric_var(returns: Series, cutoff: float = 0.05, *, method: str = "gaussian") -> float:
    """Value at risk per bar from the mean and deviation, Gaussian or Cornish-Fisher."""
    if method not in VAR_METHODS:
        raise ValueError(f"Unknown method {method!r}; use {' or '.join(VAR_METHODS)}.")
    z = _quantile(cutoff)
    values = finite(returns)
    if values.size < 2:
        return math.nan
    if method == "cornish_fisher":
        s, k = skewness(values), excess_kurtosis(values)
        if math.isnan(s) or math.isnan(k):
            return math.nan
        z = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
    return float(values.mean()) + z * float(np.std(values, ddof=1))


def parametric_expected_shortfall(returns: Series, cutoff: float = 0.05) -> float:
    """Gaussian expected shortfall per bar: the mean return below the Gaussian value at risk."""
    z = _quantile(cutoff)
    values = finite(returns)
    if values.size < 2:
        return math.nan
    density = math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)
    return float(values.mean()) - float(np.std(values, ddof=1)) * density / cutoff


def _quantile(cutoff: float) -> float:
    if not 0.0 < cutoff < 1.0:
        raise ValueError(f"cutoff must be between 0 and 1, got {cutoff}.")
    return normal_ppf(cutoff)
