"""Cross-sectional signal hygiene: z-scores, winsorizing, and neutralizing, one bar at a time.

Conventions:
- Every transform works across the assets of one bar (one row of bars by assets) and never
  across bars, so a signal known at the close of bar t stays known at that close: no look-ahead.
- Missing values (NaN or infinite) stay NaN and are left out of every statistic.
- `zscore` uses the population standard deviation (``ddof=0``); a bar with fewer than two
  values, or whose values are all equal, is NaN.
- `winsorize` clips each bar to its own ``lower`` and ``upper`` quantiles, interpolated
  linearly (numpy's default), so its effect is the same at any signal scale.
- `demean_by_group` subtracts each group's mean in the bar, such as a sector's, so the signal
  ranks assets within their group.
- `neutralize` keeps the residual of a per-bar least-squares fit of the signal on the
  exposures, with an intercept, so the result has zero mean and no linear exposure to them.
  A bar with no more valid assets than coefficients is NaN.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from quant_toolbox._numeric import FLAT
from quant_toolbox._types import FloatArray


def zscore(signal: FloatArray) -> FloatArray:
    """Each bar's signal minus its mean, over its standard deviation (``ddof=0``)."""
    values = np.asarray(signal, dtype=np.float64)
    valid = np.isfinite(values)
    count = valid.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(valid, values, 0.0).sum(axis=1, keepdims=True) / count
        deviation = np.where(valid, values - mean, 0.0)
        spread = np.sqrt((deviation**2).sum(axis=1, keepdims=True) / count)
        scaled = deviation / spread
    flat = spread <= FLAT * np.maximum(1.0, np.abs(mean))
    result: FloatArray = np.where(valid & (count >= 2) & ~flat, scaled, np.nan)
    return result


def winsorize(signal: FloatArray, lower: float = 0.01, upper: float = 0.99) -> FloatArray:
    """Clip each bar's signal to its ``lower`` and ``upper`` quantiles."""
    if not 0.0 <= lower < upper <= 1.0:
        raise ValueError(f"winsorize needs 0 <= lower < upper <= 1; got {lower} and {upper}.")
    values = np.asarray(signal, dtype=np.float64)
    valid = np.isfinite(values)
    result: FloatArray = np.full(values.shape, np.nan)
    rows = valid.any(axis=1)
    if rows.any():
        kept = np.where(valid[rows], values[rows], np.nan)
        low = np.nanquantile(kept, lower, axis=1, keepdims=True)
        high = np.nanquantile(kept, upper, axis=1, keepdims=True)
        result[rows] = np.where(valid[rows], np.clip(kept, low, high), np.nan)
    return result


def demean_by_group(signal: FloatArray, groups: Sequence[str]) -> FloatArray:
    """Each bar's signal minus the mean of its group in that bar; ``groups`` labels each asset."""
    values = np.asarray(signal, dtype=np.float64)
    if len(groups) != values.shape[1]:
        raise ValueError(
            f"groups needs one label per asset: {len(groups)} labels for {values.shape[1]} assets."
        )
    valid = np.isfinite(values)
    labels, index = np.unique(np.asarray(groups, dtype=str), return_inverse=True)
    members = np.eye(labels.size, dtype=np.float64)[index]  # assets x groups
    with np.errstate(invalid="ignore", divide="ignore"):
        means = (np.where(valid, values, 0.0) @ members) / (valid.astype(np.float64) @ members)
    result: FloatArray = np.where(valid, values - means[:, index], np.nan)
    return result


def neutralize(signal: FloatArray, exposures: FloatArray) -> FloatArray:
    """The residual of each bar's signal on ``exposures`` and an intercept (least squares).

    ``exposures`` is bars by assets by factors, or assets by factors when constant over time,
    such as industry dummies or betas known at each bar's close. An asset with a missing
    exposure is left out of its bar, and is NaN there.
    """
    values = np.asarray(signal, dtype=np.float64)
    loads = np.asarray(exposures, dtype=np.float64)
    if loads.ndim == 2:
        loads = np.broadcast_to(loads, (values.shape[0], *loads.shape))
    if loads.ndim != 3 or loads.shape[:2] != values.shape:
        raise ValueError(
            "exposures must be assets by factors, or bars by assets by factors, matching the "
            f"signal's {values.shape}; got {np.shape(exposures)}."
        )
    result: FloatArray = np.full(values.shape, np.nan)
    valid = np.isfinite(values) & np.isfinite(loads).all(axis=2)
    coefficients = loads.shape[2] + 1
    for bar in np.flatnonzero(valid.sum(axis=1) > coefficients):
        keep = valid[bar]
        design = np.column_stack([np.ones(int(keep.sum())), loads[bar, keep]])
        fit, *_ = np.linalg.lstsq(design, values[bar, keep], rcond=None)
        result[bar, keep] = values[bar, keep] - design @ fit
    return result
