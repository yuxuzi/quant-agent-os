"""Portfolio weights from a signal: dollar-neutral, beta-neutral, and long-only; market returns.

Every function is known at the close of its bar, so weights built from it never look ahead.
"""

from __future__ import annotations

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import bar_returns
from quant_toolbox.research._evaluation import DELISTING_RETURN


def dollar_neutral_weights(signal: FloatArray) -> FloatArray:
    """Dollar-neutral weights from cross-sectional signal ranks, with gross exposure 1."""
    weights = np.zeros(signal.shape)
    for bar in range(signal.shape[0]):
        valid = np.isfinite(signal[bar])
        if int(valid.sum()) < 2:
            continue
        values = signal[bar][valid]
        order = np.argsort(np.argsort(values, kind="mergesort"), kind="mergesort")
        centred = order - order.mean()
        gross = float(np.abs(centred).sum())
        if gross > 0.0:
            weights[bar, valid] = centred / gross
    return weights


def market_returns(prices: FloatArray, delisting_return: float = DELISTING_RETURN) -> FloatArray:
    """Equal-weight market return per bar: the mean over assets priced at the previous close."""
    returns, _ = bar_returns(prices, delisting_return)
    market = np.zeros(prices.shape[0])
    priced = np.isfinite(prices)
    for bar in range(1, prices.shape[0]):
        held = priced[bar - 1]
        if held.any():
            market[bar] = float(returns[bar, held].mean())
    return market


def rolling_betas(prices: FloatArray, window: int, market: FloatArray | None = None) -> FloatArray:
    """Each asset's beta to the market over the ``window`` bars ending at t (NaN before then).

    Known at the close of bar t, so weights built from it never look ahead.
    """
    returns, _ = bar_returns(prices)
    market = market_returns(prices) if market is None else market
    betas = np.full(prices.shape, np.nan)
    for bar in range(window, prices.shape[0]):
        x = market[bar - window + 1 : bar + 1]
        x = x - x.mean()
        spread = float(x @ x)
        if spread > 0.0:
            y = returns[bar - window + 1 : bar + 1]
            betas[bar] = x @ (y - y.mean(axis=0)) / spread
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
