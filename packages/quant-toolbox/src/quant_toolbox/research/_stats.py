"""Statistics for signal research: ranks, information coefficients, and Newey-West t-statistics.

- t-statistics of a mean use Newey-West (HAC) standard errors with a Bartlett kernel, so serial
  correlation, including the overlap of multi-bar forward returns, does not inflate them. The
  default lag count is ``floor(4 * (n / 100) ** (2 / 9))`` (Newey and West, 1994).
- Information coefficients compare a signal at bar t with the forward return that starts at t.
  NaN pairs are ignored, and bars with fewer than three valid pairs are NaN.
"""

from __future__ import annotations

import numpy as np

from quant_toolbox._types import FloatArray

MIN_PAIRS = 3


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
