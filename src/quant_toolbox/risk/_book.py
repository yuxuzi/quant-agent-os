"""A book of positions: exposures, concentration, simulated history, risk shares, and stress.

Conventions:
- Positions are weights: market value over NAV, with shorts negative. Gross is the sum of absolute
  weights, and net is their sum. NaN counts as no position.
- Return-based risk is historical simulation: today's weights applied to each past bar's asset
  returns. It describes today's book, not what the fund earned. A missing asset return counts
  as 0 in that bar.
- A risk share is ``w_i * (C w)_i / (w' C w)``, with C the sample covariance of asset returns, so
  shares sum to 1 (Euler). A book with no variance, or only rounding left over from an exact
  hedge (see `FLAT`), has shares of 0.
- A stress is a return per asset applied to today's weights, linearly: no second-order effects.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.risk._decomposition import FLAT

UNGROUPED = "Ungrouped"


@dataclass(frozen=True)
class Exposures:
    """The size and concentration of a book."""

    gross: float
    net: float
    long: float
    short: float
    positions: int
    largest: float  # the largest absolute weight
    top_five: float  # the sum of the five largest absolute weights
    effective_positions: float  # 1 / sum of squared shares of gross


def exposures(weights: FloatArray) -> Exposures:
    """Measure one row of weights; NaN counts as no position."""
    values = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    sizes = np.sort(np.abs(values))[::-1]
    gross = float(sizes.sum())
    effective = 1.0 / float(np.sum((sizes / gross) ** 2)) if gross > 0.0 else 0.0
    return Exposures(
        gross=gross,
        net=float(values.sum()),
        long=float(values[values > 0.0].sum()),
        short=float(values[values < 0.0].sum()),
        positions=int(np.count_nonzero(values)),
        largest=float(sizes[0]) if sizes.size else 0.0,
        top_five=float(sizes[:5].sum()),
        effective_positions=effective,
    )


def group_exposures(
    assets: list[str], weights: FloatArray, groups: dict[str, str]
) -> dict[str, tuple[float, float]]:
    """Return ``{group: (net, gross)}`` for positions held, largest gross first."""
    totals: dict[str, tuple[float, float]] = {}
    for asset, weight in zip(assets, np.nan_to_num(weights), strict=True):
        if weight == 0.0:
            continue
        group = groups.get(asset, UNGROUPED)
        net, gross = totals.get(group, (0.0, 0.0))
        totals[group] = (net + float(weight), gross + abs(float(weight)))
    return dict(sorted(totals.items(), key=lambda item: -item[1][1]))


def simulated_returns(weights: FloatArray, asset_returns: FloatArray) -> FloatArray:
    """Today's ``weights`` applied to each past bar's returns (bars by assets)."""
    result: FloatArray = np.nan_to_num(asset_returns) @ np.nan_to_num(weights)
    return result


def risk_shares(weights: FloatArray, asset_returns: FloatArray) -> FloatArray:
    """Each asset's share of the book's variance; they sum to 1, and are 0 for a riskless book."""
    held = np.nan_to_num(np.asarray(weights, dtype=np.float64))
    returns = np.nan_to_num(np.asarray(asset_returns, dtype=np.float64))
    if returns.ndim != 2 or returns.shape[0] < 2 or not returns.shape[1]:
        return np.zeros(held.shape)
    covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))
    marginal = covariance @ held
    variance = float(held @ marginal)
    scale = float(np.abs(held) @ np.sqrt(np.clip(np.diag(covariance), 0.0, None)))
    if variance <= 0.0 or np.sqrt(variance) <= FLAT * scale:
        return np.zeros(held.shape)
    shares: FloatArray = held * marginal / variance
    return shares


def stress_return(weights: FloatArray, shocks: FloatArray) -> float:
    """The book's return when each asset moves by its shock: the weights times the shocks."""
    return float(np.nan_to_num(np.asarray(weights, dtype=np.float64)) @ np.asarray(shocks))
