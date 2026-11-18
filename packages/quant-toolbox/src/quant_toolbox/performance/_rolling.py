"""Rolling metrics as values, one per bar, vectorised over windows.

Conventions:
- The value at bar t uses the ``window`` bars ending at t, so it is known at t's close; bars
  before the first full window are NaN, and the output has one value per input bar.
- A window holding a missing value (NaN) is NaN: nothing is filled or skipped inside a window.
- Annualisation uses ``periods_per_year``, never the window length: volatility is the sample
  standard deviation times ``sqrt(periods_per_year)``, and Sharpe is the mean excess return over
  its sample standard deviation, times ``sqrt(periods_per_year)``, with the annual risk-free rate
  divided by ``periods_per_year``. These match `annualized_volatility` and `sharpe_ratio` on the
  same window.
- Beta is the OLS slope on the benchmark, and correlation is Pearson's. A ratio whose
  denominator does not vary over the window (Sharpe, beta, correlation) is NaN; the volatility
  of a constant window is 0.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from quant_toolbox._numeric import FLAT
from quant_toolbox._types import BoolArray, FloatArray

Series = FloatArray | Sequence[float]


def rolling_volatility(returns: Series, window: int, periods_per_year: int) -> FloatArray:
    """Annualised sample volatility over each trailing ``window`` of bars."""
    values = _series(returns)
    result = _empty(values, window)
    if values.size >= window:
        result[window - 1 :] = np.std(_windows(values, window), axis=1, ddof=1) * np.sqrt(
            periods_per_year
        )
    return result


def rolling_sharpe(
    returns: Series, window: int, periods_per_year: int, risk_free: float = 0.0
) -> FloatArray:
    """Annualised Sharpe ratio over each trailing ``window``; ``risk_free`` is an annual rate."""
    values = _series(returns) - risk_free / periods_per_year
    result = _empty(values, window)
    if values.size >= window:
        frames = _windows(values, window)
        mean = frames.mean(axis=1)
        deviation = frames.std(axis=1, ddof=1)
        flat = _flat(deviation, mean)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = mean / deviation * np.sqrt(periods_per_year)
        result[window - 1 :] = np.where(flat, np.nan, ratio)
    return result


def rolling_beta(returns: Series, benchmark: Series, window: int) -> FloatArray:
    """OLS slope of ``returns`` on ``benchmark`` over each trailing ``window``."""
    y, x = _two(returns, benchmark)
    result = _empty(y, window)
    if y.size >= window:
        ys, xs = _windows(y, window), _windows(x, window)
        dx = xs - xs.mean(axis=1, keepdims=True)
        dy = ys - ys.mean(axis=1, keepdims=True)
        spread = np.sum(dx * dx, axis=1)
        flat = _flat(xs.std(axis=1, ddof=1), xs.mean(axis=1))
        with np.errstate(divide="ignore", invalid="ignore"):
            slope = np.sum(dx * dy, axis=1) / spread
        result[window - 1 :] = np.where(flat, np.nan, slope)
    return result


def rolling_correlation(returns: Series, benchmark: Series, window: int) -> FloatArray:
    """Pearson correlation of ``returns`` with ``benchmark`` over each trailing ``window``."""
    y, x = _two(returns, benchmark)
    result = _empty(y, window)
    if y.size >= window:
        ys, xs = _windows(y, window), _windows(x, window)
        dx = xs - xs.mean(axis=1, keepdims=True)
        dy = ys - ys.mean(axis=1, keepdims=True)
        flat = _flat(xs.std(axis=1, ddof=1), xs.mean(axis=1)) | _flat(
            ys.std(axis=1, ddof=1), ys.mean(axis=1)
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.sum(dx * dy, axis=1) / np.sqrt(np.sum(dx * dx, axis=1) * np.sum(dy * dy, axis=1))
        result[window - 1 :] = np.where(flat, np.nan, np.clip(r, -1.0, 1.0))
    return result


def _series(values: Series) -> FloatArray:
    data: FloatArray = np.asarray(values, dtype=np.float64).ravel()
    return data


def _two(left: Series, right: Series) -> tuple[FloatArray, FloatArray]:
    y, x = _series(left), _series(right)
    if y.shape != x.shape:
        raise ValueError("The two series need one value per bar each.")
    return y, x


def _empty(values: FloatArray, window: int) -> FloatArray:
    if window < 2:
        raise ValueError(f"A rolling window needs at least 2 bars, got {window}.")
    return np.full(values.shape, np.nan)


def _windows(values: FloatArray, window: int) -> FloatArray:
    frames: FloatArray = sliding_window_view(values, window)
    return frames


def _flat(deviation: FloatArray, mean: FloatArray) -> BoolArray:
    """Windows whose values vary by no more than floating-point noise (NaN windows excluded)."""
    flat: BoolArray = deviation <= FLAT * np.maximum(1.0, np.abs(mean))
    return flat
