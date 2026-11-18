"""The 0.5.0 loop implementations, kept as oracles: the vectorised versions must equal them."""

from __future__ import annotations

import numpy as np

from quant_toolbox.performance import bar_returns
from quant_toolbox.research import DELISTING_RETURN, MIN_PAIRS, TRADE_LAG, forward_returns, ranks


def information_coefficient(signal, forward, *, rank=False):  # type: ignore[no-untyped-def]
    coefficients = np.full(signal.shape[0], np.nan)
    for bar in range(signal.shape[0]):
        valid = np.isfinite(signal[bar]) & np.isfinite(forward[bar])
        if int(valid.sum()) < MIN_PAIRS:
            continue
        left, right = signal[bar][valid], forward[bar][valid]
        if rank:
            left, right = ranks(left), ranks(right)
        if np.std(left) == 0.0 or np.std(right) == 0.0:
            continue
        coefficients[bar] = float(np.corrcoef(left, right)[0, 1])
    return coefficients


def quantile_spread(signal, prices, quantiles=5, lag=TRADE_LAG, delisting_return=DELISTING_RETURN):  # type: ignore[no-untyped-def]
    forward = forward_returns(prices, 1, lag, delisting_return)
    spread = np.full(signal.shape[0], np.nan)
    for bar in range(signal.shape[0]):
        valid = np.isfinite(signal[bar]) & np.isfinite(forward[bar])
        if int(valid.sum()) < 2 * quantiles:
            continue
        bucket = np.floor((ranks(signal[bar][valid]) - 1) * quantiles / int(valid.sum()))
        returns = forward[bar][valid]
        spread[bar] = float(returns[bucket == quantiles - 1].mean() - returns[bucket == 0].mean())
    return spread


def dollar_neutral_weights(signal):  # type: ignore[no-untyped-def]
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


def market_returns(prices, delisting_return=DELISTING_RETURN):  # type: ignore[no-untyped-def]
    returns, _ = bar_returns(prices, delisting_return)
    market = np.zeros(prices.shape[0])
    priced = np.isfinite(prices)
    for bar in range(1, prices.shape[0]):
        held = priced[bar - 1]
        if held.any():
            market[bar] = float(returns[bar, held].mean())
    return market


def rolling_betas(prices, window, market=None):  # type: ignore[no-untyped-def]
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


def long_only_weights(signal, quantiles=5):  # type: ignore[no-untyped-def]
    weights = np.zeros(signal.shape)
    for bar in range(signal.shape[0]):
        valid = np.flatnonzero(np.isfinite(signal[bar]))
        if valid.size == 0:
            continue
        count = max(1, valid.size // quantiles)
        top = valid[np.argsort(signal[bar, valid], kind="mergesort")[-count:]]
        weights[bar, top] = 1.0 / count
    return weights
