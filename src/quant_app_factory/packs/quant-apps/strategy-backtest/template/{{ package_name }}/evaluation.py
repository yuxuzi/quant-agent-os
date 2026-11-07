"""Signal evaluation: information coefficients, quantile spreads, decay, and turnover.

Timing convention, shared with backtests: a signal is known at the close of bar t, and a trade
on it happens ``lag`` bars later, at the close of bar t + lag. So the signal at bar t is scored
against the return from bar t + lag to bar t + lag + horizon. ``lag=0`` trades at the signal's
own close (the most optimistic choice); the default, ``config.TRADE_LAG``, is what a backtest
of this signal would assume.

Missing prices follow the backtest too: a signal needs a price at the trade bar to count, a gap
is bridged by the last known price, and an asset delisted inside the horizon earns
``config.DELISTING_RETURN`` rather than dropping out of the cross-section.
"""

from __future__ import annotations

import math

import numpy as np

from . import config
from .metrics import (
    FloatArray,
    bar_returns,
    information_coefficient,
    newey_west_lags,
    newey_west_t_stat,
    ranks,
)


def forward_returns(
    prices: FloatArray,
    horizon: int = 1,
    lag: int = config.TRADE_LAG,
    delisting_return: float = config.DELISTING_RETURN,
) -> FloatArray:
    """Return from bar t + lag to bar t + lag + horizon, aligned to bar t.

    NaN where there is no price to trade at bar t + lag, and for the last bars.
    """
    if horizon < 1 or lag < 0:
        raise ValueError("horizon must be at least one bar and lag zero or more bars.")
    forward = np.full(prices.shape, np.nan)
    reach = lag + horizon
    if prices.shape[0] > reach:
        returns, _ = bar_returns(prices, delisting_return)
        wealth = np.cumprod(1.0 + returns, axis=0)
        start = wealth[lag : prices.shape[0] - horizon]
        tradable = np.isfinite(prices[lag : prices.shape[0] - horizon]) & (start > 0.0)
        moved = np.divide(wealth[reach:], start, out=np.ones(start.shape), where=tradable) - 1.0
        forward[:-reach] = np.where(tradable, moved, np.nan)
    return forward


def ic_summary(
    signal: FloatArray,
    prices: FloatArray,
    horizon: int = 1,
    lag: int = config.TRADE_LAG,
    delisting_return: float = config.DELISTING_RETURN,
) -> dict[str, float]:
    """Mean IC, mean rank IC, its HAC t-statistic, and the share of bars with a positive IC."""
    forward = forward_returns(prices, horizon, lag, delisting_return)
    pearson = information_coefficient(signal, forward)
    spearman = information_coefficient(signal, forward, rank=True)
    valid = spearman[np.isfinite(spearman)]
    return {
        "mean_ic": float(np.nanmean(pearson)) if np.isfinite(pearson).any() else math.nan,
        "mean_rank_ic": float(np.mean(valid)) if valid.size else math.nan,
        "rank_ic_t_stat": hac_t_stat(valid, horizon),
        "positive_rank_ic_share": float(np.mean(valid > 0.0)) if valid.size else math.nan,
        "bars": float(valid.size),
    }


def hac_t_stat(ics: FloatArray, horizon: int) -> float:
    """Newey-West t-statistic of a mean IC; the lag count covers the ``horizon - 1`` overlap."""
    valid = ics[np.isfinite(ics)]
    return newey_west_t_stat(valid, max(horizon - 1, newey_west_lags(valid.size)))


def quantile_spread(
    signal: FloatArray,
    prices: FloatArray,
    quantiles: int = 5,
    lag: int = config.TRADE_LAG,
    delisting_return: float = config.DELISTING_RETURN,
) -> FloatArray:
    """Per-bar return of the top signal quantile minus the bottom one, one bar after the trade."""
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


def ic_decay(
    signal: FloatArray,
    prices: FloatArray,
    max_horizon: int,
    lag: int = config.TRADE_LAG,
    delisting_return: float = config.DELISTING_RETURN,
) -> dict[str, list[float]]:
    """Mean rank IC and its HAC t-statistic at horizons 1 to ``max_horizon`` bars.

    Each horizon is measured from the trade. Forward returns over h bars overlap for h - 1
    bars, so neighbouring ICs are correlated; the t-statistics account for it.
    """
    means, t_stats = [], []
    for horizon in range(1, max_horizon + 1):
        forward = forward_returns(prices, horizon, lag, delisting_return)
        ic = information_coefficient(signal, forward, rank=True)
        means.append(float(np.nanmean(ic)) if np.isfinite(ic).any() else math.nan)
        t_stats.append(hac_t_stat(ic, horizon))
    return {"mean_rank_ic": means, "t_stat": t_stats}


def signal_autocorrelation(signal: FloatArray, lag: int = 1) -> float:
    """Mean rank correlation of the signal with itself ``lag`` bars earlier (turnover proxy)."""
    if signal.shape[0] <= lag:
        return math.nan
    ic = information_coefficient(signal[lag:], signal[:-lag], rank=True)
    return float(np.nanmean(ic)) if np.isfinite(ic).any() else math.nan
