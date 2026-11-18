"""Portfolio weights from a signal: dollar-neutral, beta-neutral, and long-only; market returns.

Every function is known at the close of its bar, so weights built from it never look ahead.
"""

from __future__ import annotations

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import bar_returns
from quant_toolbox.research._evaluation import DELISTING_RETURN


def dollar_neutral_weights(signal: FloatArray) -> FloatArray:
    """Dollar-neutral weights from cross-sectional signal ranks, with gross exposure 1.

    Each bar's valid signals are ranked 0 to n - 1 (ties in column order), centred, and scaled
    so absolute weights sum to 1; a bar with fewer than two valid signals holds nothing.
    """
    values = np.asarray(signal, dtype=np.float64)
    valid = np.isfinite(values)
    count = valid.sum(axis=1)
    order = _ordinal(np.where(valid, values, np.inf))
    centred = np.where(valid, order - (count[:, None] - 1) / 2.0, 0.0)
    gross = np.abs(centred).sum(axis=1)
    held = (count >= 2) & (gross > 0.0)
    weights: FloatArray = np.zeros(values.shape)
    weights[held] = centred[held] / gross[held, None]
    return weights


def _ordinal(values: FloatArray) -> FloatArray:
    """Each row's 0-based ordinal ranks, ties broken by column order (a stable sort)."""
    order = np.argsort(values, axis=1, kind="mergesort")
    ranks = np.empty(values.shape)
    np.put_along_axis(ranks, order, np.broadcast_to(np.arange(values.shape[1]), values.shape), 1)
    return ranks


def market_returns(prices: FloatArray, delisting_return: float = DELISTING_RETURN) -> FloatArray:
    """Equal-weight market return per bar: the mean over assets priced at the previous close."""
    returns, _ = bar_returns(prices, delisting_return)
    market: FloatArray = np.zeros(prices.shape[0])
    held = np.isfinite(prices[:-1])
    count = held.sum(axis=1)
    total = np.where(held, returns[1:], 0.0).sum(axis=1)
    market[1:] = np.divide(total, count, out=np.zeros(count.shape), where=count > 0)
    return market


def rolling_betas(prices: FloatArray, window: int, market: FloatArray | None = None) -> FloatArray:
    """Each asset's beta to the market over the ``window`` bars ending at t (NaN before then).

    Known at the close of bar t, so weights built from it never look ahead.
    """
    returns, _ = bar_returns(prices)
    market = market_returns(prices) if market is None else np.asarray(market, dtype=np.float64)
    bars = prices.shape[0]
    betas: FloatArray = np.full(prices.shape, np.nan)
    if window < 1 or bars <= window:
        return betas
    windows = np.lib.stride_tricks.sliding_window_view(market[1:], window)  # ends at bars 1..
    centred = windows - windows.mean(axis=1, keepdims=True)
    spread = np.einsum("ij,ij->i", centred, centred)
    covariance = np.zeros((windows.shape[0], prices.shape[1]))
    total = np.zeros(covariance.shape)
    for k in range(window):  # sum over each window's bars of centred market times returns
        covariance += centred[:, k, None] * returns[1 + k : 1 + k + windows.shape[0]]
        total += returns[1 + k : 1 + k + windows.shape[0]]
    covariance -= centred.sum(axis=1)[:, None] * total / window  # centring the returns too
    ends = np.arange(window, window + windows.shape[0])  # bar t of window ending at t
    keep = (spread > 0.0) & (ends < bars)
    betas[ends[keep]] = covariance[keep] / spread[keep, None]
    return betas


def beta_neutral_weights(signal: FloatArray, betas: FloatArray) -> FloatArray:
    """Rank weights with zero net dollars and zero beta, scaled to gross exposure 1."""
    ranked = dollar_neutral_weights(np.where(np.isfinite(betas), signal, np.nan))
    weights = np.zeros(signal.shape)
    for bar in range(signal.shape[0]):
        valid = (ranked[bar] != 0.0) & np.isfinite(betas[bar])
        if int(valid.sum()) < 3:
            continue
        exposures = np.column_stack([np.ones(int(valid.sum())), betas[bar, valid]])
        fit, *_ = np.linalg.lstsq(exposures, ranked[bar, valid], rcond=None)
        neutral = ranked[bar, valid] - exposures @ fit
        gross = float(np.abs(neutral).sum())
        if gross > 1e-12:
            weights[bar, valid] = neutral / gross
    return weights


def long_only_weights(signal: FloatArray, quantiles: int = 5) -> FloatArray:
    """Equal weights on the top 1 / ``quantiles`` of assets by signal, summing to 1."""
    weights = np.zeros(signal.shape)
    for bar in range(signal.shape[0]):
        valid = np.flatnonzero(np.isfinite(signal[bar]))
        if valid.size == 0:
            continue
        count = max(1, valid.size // quantiles)
        top = valid[np.argsort(signal[bar, valid], kind="mergesort")[-count:]]
        weights[bar, top] = 1.0 / count
    return weights
