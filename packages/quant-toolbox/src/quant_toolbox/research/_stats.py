"""Statistics for signal research: ranks, information coefficients, and Newey-West t-statistics.

- t-statistics of a mean use Newey-West (HAC) standard errors with a Bartlett kernel, so serial
  correlation, including the overlap of multi-bar forward returns, does not inflate them. The
  default lag count is ``floor(4 * (n / 100) ** (2 / 9))`` (Newey and West, 1994).
- Information coefficients compare a signal at bar t with the forward return that starts at t.
  NaN pairs are ignored, and bars with fewer than three valid pairs are NaN.
"""

from __future__ import annotations

import numpy as np

from quant_toolbox._types import BoolArray, FloatArray

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

    ``rank=True`` gives the rank (Spearman) coefficient, ranking each bar's valid pairs with
    tied values at their average rank; otherwise Pearson. A bar whose signal or returns are all
    equal has no coefficient (NaN).
    """
    left = np.asarray(signal, dtype=np.float64)
    right = np.asarray(forward_returns, dtype=np.float64)
    valid = np.isfinite(left) & np.isfinite(right)
    enough = valid.sum(axis=1) >= MIN_PAIRS
    if rank:
        left, right = row_ranks(left, valid), row_ranks(right, valid)
    return np.where(
        enough & _varies(left, valid) & _varies(right, valid), _pearson(left, right, valid), np.nan
    )


def row_ranks(values: FloatArray, valid: BoolArray) -> FloatArray:
    """Rank each row's ``valid`` values from 1, ties at their average rank; NaN elsewhere."""
    data = np.where(valid, values, np.inf)
    rows, columns = data.shape
    if rows == 0 or columns == 0:
        return np.full(data.shape, np.nan)
    order = np.argsort(data, axis=1, kind="mergesort")
    ordered = np.take_along_axis(data, order, axis=1)
    starts = np.ones(ordered.shape, dtype=bool)
    starts[:, 1:] = ordered[:, 1:] != ordered[:, :-1]
    flat = starts.ravel()
    group = np.cumsum(flat) - 1  # one id per run of equal values, unique across rows
    first = np.tile(np.arange(columns), rows)[flat]
    last = first + np.diff(np.append(np.flatnonzero(flat), flat.size)) - 1
    average = 0.5 * (first + last + 2).astype(np.float64)
    ranked = np.empty(data.shape)
    np.put_along_axis(ranked, order, average[group].reshape(data.shape), axis=1)
    result: FloatArray = np.where(valid, ranked, np.nan)
    return result


def _varies(values: FloatArray, valid: BoolArray) -> BoolArray:
    """Rows whose valid values are not all equal."""
    high = np.where(valid, values, -np.inf).max(axis=1, initial=-np.inf)
    low = np.where(valid, values, np.inf).min(axis=1, initial=np.inf)
    result: BoolArray = high > low
    return result


def _pearson(left: FloatArray, right: FloatArray, valid: BoolArray) -> FloatArray:
    """Each row's Pearson correlation over its valid pairs (NaN where undefined)."""
    count = valid.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.where(valid, left, 0.0)
        y = np.where(valid, right, 0.0)
        dx = np.where(valid, x - (x.sum(axis=1) / count)[:, None], 0.0)
        dy = np.where(valid, y - (y.sum(axis=1) / count)[:, None], 0.0)
        result: FloatArray = np.einsum("ij,ij->i", dx, dy) / np.sqrt(
            np.einsum("ij,ij->i", dx, dx) * np.einsum("ij,ij->i", dy, dy)
        )
    return np.clip(result, -1.0, 1.0)
