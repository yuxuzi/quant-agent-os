"""How much a Sharpe ratio can be trusted: its standard error, PSR, DSR, and track record length.

Conventions:
- Every statistic works on the per-bar Sharpe ratio ``SR = mean(r) / std(r, ddof=1)`` of the
  returns given, with no risk-free rate: pass excess returns to measure excess Sharpe. Annual
  Sharpe ratios given or returned convert by ``sqrt(periods_per_year)``; a variance of annual
  Sharpe ratios converts by ``periods_per_year``.
- Skewness ``g3`` and kurtosis ``g4`` (not excess: 3 for a normal distribution) are population
  moments, as in Bailey and López de Prado (2012). Fat tails and negative skew widen every
  interval below.
- Standard error (Mertens, 2002): ``sqrt((1 - g3 SR + (g4 - 1) / 4 SR^2) / (n - 1))`` per bar,
  times ``sqrt(periods_per_year)`` for the annual Sharpe ratio.
- Probabilistic Sharpe ratio (Bailey and López de Prado, 2012): the probability that the true
  Sharpe ratio exceeds a benchmark, ``Phi((SR - SR*) / standard error)``. Always in [0, 1].
- Deflated Sharpe ratio (Bailey and López de Prado, 2014): the probabilistic Sharpe ratio against
  the Sharpe ratio the best of ``trials`` unskilled strategies would show,
  ``SR0 = sqrt(V) ((1 - e) Phi^-1(1 - 1/N) + e Phi^-1(1 - 1/(N exp(1))))``, with V the variance
  of the trials' Sharpe ratios and e the Euler-Mascheroni constant.
- Minimum track record length: the bars needed for the probabilistic Sharpe ratio to reach
  ``confidence``, ``1 + (1 - g3 SR + (g4 - 1) / 4 SR^2) (Phi^-1(confidence) / (SR - SR*))^2``.
- Undefined is NaN: fewer than three returns, returns that do not vary, or (for the track
  record) a Sharpe ratio not above the benchmark.
"""

from __future__ import annotations

import math

import numpy as np

from quant_toolbox._numeric import finite, varies
from quant_toolbox._types import FloatArray

EULER_MASCHERONI = 0.5772156649015329
MIN_RETURNS = 3

_A = (-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.3577518672690,
      -30.66479806614716, 2.506628277459239)  # fmt: skip
_B = (-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972,
      -13.28068155288572)  # fmt: skip
_C = (-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734,
      4.374664141464968, 2.938163982698783)  # fmt: skip
_D = (0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416)


def normal_cdf(x: float) -> float:
    """The standard normal distribution function, accurate in both tails."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def normal_ppf(p: float) -> float:
    """The standard normal quantile: Acklam's approximation, refined by one Halley step.

    -inf at 0 and inf at 1; ValueError outside [0, 1].
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"A probability must be between 0 and 1, got {p}.")
    if p == 0.0:
        return -math.inf
    if p == 1.0:
        return math.inf
    if p > 0.5:  # by symmetry, so the refinement below runs where p has full precision
        return -normal_ppf(1.0 - p)
    if p < 0.02425:
        x = _tail(math.sqrt(-2.0 * math.log(p)))
    else:
        q = p - 0.5
        r = q * q
        a, b = _A, _B
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        x /= ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    error = normal_cdf(x) - p
    u = error * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def _tail(q: float) -> float:
    c, d = _C, _D
    top = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
    return top / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def sharpe_standard_error(returns: FloatArray | list[float], periods_per_year: int) -> float:
    """Standard error of the annualised Sharpe ratio, allowing for skew and fat tails."""
    moments = _moments(returns)
    if moments is None:
        return math.nan
    _, n, spread = moments
    return math.sqrt(spread / (n - 1)) * math.sqrt(periods_per_year) if spread > 0 else math.nan


def probabilistic_sharpe_ratio(
    returns: FloatArray | list[float], periods_per_year: int, benchmark_sharpe: float = 0.0
) -> float:
    """Probability that the true annual Sharpe ratio exceeds ``benchmark_sharpe`` (annual)."""
    moments = _moments(returns)
    if moments is None:
        return math.nan
    sharpe, n, spread = moments
    return _probabilistic(sharpe, n, spread, benchmark_sharpe / math.sqrt(periods_per_year))


def deflated_sharpe_ratio(
    returns: FloatArray | list[float],
    periods_per_year: int,
    trials: int,
    trial_sharpe_variance: float,
) -> float:
    """Probabilistic Sharpe ratio against the best of ``trials`` unskilled strategies.

    ``trial_sharpe_variance`` is the variance of the annual Sharpe ratios of all the trials,
    this one included. Use it when the strategy was chosen among many tried.
    """
    if trials < 2:
        raise ValueError(f"trials must be at least 2, got {trials}; with one, use PSR.")
    if trial_sharpe_variance < 0.0:
        raise ValueError("trial_sharpe_variance cannot be negative.")
    moments = _moments(returns)
    if moments is None:
        return math.nan
    sharpe, n, spread = moments
    threshold = expected_maximum_sharpe(trials, trial_sharpe_variance / periods_per_year)
    return _probabilistic(sharpe, n, spread, threshold)


def expected_maximum_sharpe(trials: int, variance: float) -> float:
    """The Sharpe ratio the best of ``trials`` skill-less strategies is expected to show.

    ``variance`` is the variance of their Sharpe ratios, in the same units as the answer.
    """
    if trials < 2:
        raise ValueError(f"trials must be at least 2, got {trials}.")
    e = EULER_MASCHERONI
    first = normal_ppf(1.0 - 1.0 / trials)
    second = normal_ppf(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(variance) * ((1.0 - e) * first + e * second)


def minimum_track_record(
    returns: FloatArray | list[float],
    periods_per_year: int,
    benchmark_sharpe: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """Bars of history needed to say, at ``confidence``, that the Sharpe ratio beats the benchmark.

    NaN when the observed Sharpe ratio is not above ``benchmark_sharpe`` (annual).
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence}.")
    moments = _moments(returns)
    if moments is None:
        return math.nan
    sharpe, _, spread = moments
    edge = sharpe - benchmark_sharpe / math.sqrt(periods_per_year)
    if edge <= 0.0 or spread <= 0.0:
        return math.nan
    return 1.0 + spread * (normal_ppf(confidence) / edge) ** 2


def _moments(returns: FloatArray | list[float]) -> tuple[float, int, float] | None:
    """Per-bar Sharpe, count, and ``1 - g3 SR + (g4 - 1) / 4 SR^2``; None when undefined."""
    values = finite(returns)
    n = int(values.size)
    if n < MIN_RETURNS or not varies(values):
        return None
    deviations = values - float(np.mean(values))
    m2 = float(np.mean(deviations**2))
    skew = float(np.mean(deviations**3)) / m2**1.5
    kurtosis = float(np.mean(deviations**4)) / m2**2
    sharpe = float(np.mean(values)) / float(np.std(values, ddof=1))
    return sharpe, n, _spread(sharpe, skew, kurtosis)


def _spread(sharpe: float, skew: float, kurtosis: float) -> float:
    return 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2


def _probabilistic(sharpe: float, n: int, spread: float, benchmark: float) -> float:
    """PSR from per-bar quantities; NaN when the moments give no positive variance."""
    if spread <= 0.0 or n < 2:
        return math.nan
    z = (sharpe - benchmark) * math.sqrt(n - 1) / math.sqrt(spread)
    return min(1.0, max(0.0, normal_cdf(z)))
