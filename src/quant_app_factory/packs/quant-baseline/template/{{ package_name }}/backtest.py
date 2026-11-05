"""A backtest of signal-driven weights: lagged trades, a rebalance schedule, drift, and costs.

Timing convention: a signal is known at the close of bar t. With ``lag`` bars of delay, the
weights built from it are first held over bar t + 1 + lag. ``lag=0`` trades at the signal's
own close (the most optimistic choice); the default, ``config.TRADE_LAG``, trades at the next
close. A signal study uses the same convention, so its IC describes what this backtest can earn.

Rebalancing: the book trades to its target weights every ``rebalance_every`` bars, counted from
the first close with a position to take. In between, holdings drift with prices, and every
rebalance trades the target minus the drifted holdings. Costs are charged on those trades.

Missing prices: an asset can only be traded at a close where it has a price, so a position is
carried through a gap and earns the move over the gap when prices resume. A position in an
asset whose prices stop for good earns ``config.DELISTING_RETURN`` once and is then written off,
at no trading cost.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from . import config, signals
from .data import Panel
from .metrics import FloatArray, active_metrics, bar_returns, beta, summarize
from .parameters import Parameters

WEIGHTS = {
    "dollar-neutral": "signal ranks, zero net dollars, gross exposure 1",
    "beta-neutral": (
        "signal ranks with zero net dollars and zero beta to the market (rolling betas), "
        "gross exposure 1"
    ),
    "long-only": "equal weights on the top signal quantile, against the equal-weight market",
}

LIMITATIONS = [
    "Prices are taken as given: survivorship, corporate actions, and point-in-time data are "
    "the data provider's responsibility.",
    "Costs are linear in turnover; there is no market impact, borrow cost, or capacity limit.",
    "This is a simulation. It places no orders and connects to no broker.",
]


def target_weights(signal: FloatArray) -> FloatArray:
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


def market_returns(
    prices: FloatArray, delisting_return: float = config.DELISTING_RETURN
) -> FloatArray:
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
    ranked = target_weights(np.where(np.isfinite(betas), signal, np.nan))
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


def weights_for(prices: FloatArray, signal: FloatArray, parameters: Parameters) -> FloatArray:
    """Target weights for ``parameters.exposure``."""
    if parameters.exposure == "beta-neutral":
        return beta_neutral_weights(signal, rolling_betas(prices, parameters.beta_window))
    if parameters.exposure == "long-only":
        return long_only_weights(signal, parameters.quantiles)
    return target_weights(signal)


def run_backtest(
    prices: FloatArray,
    weights: FloatArray,
    *,
    cost_bps: float,
    lag: int = config.TRADE_LAG,
    rebalance_every: int = config.REBALANCE_EVERY,
    delisting_return: float = config.DELISTING_RETURN,
) -> dict[str, FloatArray]:
    """Return per-bar gross and net returns, turnover, the weights held, and forced exits.

    ``held[t]`` is the book over bar t, as weights of the portfolio's value at the start of it.
    """
    if lag < 0:
        raise ValueError("lag must be zero or more bars.")
    if rebalance_every < 1:
        raise ValueError("rebalance_every must be at least one bar.")
    asset_returns, exits = bar_returns(prices, delisting_return)
    priced = np.isfinite(prices)
    gone = np.cumsum(exits, axis=0) > 0  # delisted on or before this bar
    held = np.zeros(weights.shape)
    gross = np.zeros(held.shape[0])
    traded = np.zeros(held.shape[0])
    drifted = np.zeros(weights.shape[1])  # the book at the last close, before trading
    first: int | None = None
    for bar in range(1, held.shape[0]):
        close = bar - 1  # trades for this bar happen at the previous close
        target = weights[close - lag] if close >= lag else np.zeros(weights.shape[1])
        if first is None and np.any(target != 0.0):
            first = close
        due = first is not None and (close - first) % rebalance_every == 0
        before = drifted * ~gone[close]  # a written-off position is simply gone
        held[bar] = np.where(due & priced[close], target, before)
        traded[bar] = float(np.abs(held[bar] - before).sum()) / 2.0
        gross[bar] = float(held[bar] @ asset_returns[bar])
        growth = 1.0 + gross[bar]
        if growth > 0.0:
            drifted = held[bar] * (1.0 + asset_returns[bar]) / growth
        else:  # the book lost everything
            drifted = np.zeros(weights.shape[1])
    net = gross - traded * cost_bps / 10_000.0
    forced = (exits & (held != 0.0)).sum(axis=1).astype(np.float64)
    return {"gross": gross, "net": net, "turnover": traded, "held": held, "forced_exits": forced}


def walk_forward_splits(
    n_bars: int, train: int, test: int, step: int | None = None, start: int = 0
) -> list[tuple[range, range]]:
    """Rolling ``(train, test)`` bar ranges from bar ``start``; each test follows its train."""
    if train <= 0 or test <= 0:
        raise ValueError("train and test must be positive numbers of bars.")
    splits = []
    while start + train + test <= n_bars:
        splits.append((range(start, start + train), range(start + train, start + train + test)))
        start += step or test
    return splits


def run(panel: Panel, parameters: Parameters | None = None) -> dict[str, Any]:
    """Backtest the signal in ``parameters`` on ``panel`` and return a results document."""
    chosen = parameters or Parameters()
    signal_parameters = signals.parameters(
        chosen.signal, lookback=chosen.lookback, skip=chosen.skip
    )
    signal = signals.compute(chosen.signal, panel.prices, **signal_parameters)
    result = run_backtest(
        panel.prices,
        weights_for(panel.prices, signal, chosen),
        cost_bps=chosen.cost_bps,
        lag=chosen.lag,
        rebalance_every=chosen.rebalance_every,
        delisting_return=chosen.delisting_return,
    )
    periods = config.PERIODS_PER_YEAR
    market = market_returns(panel.prices, chosen.delisting_return)
    exposure_metrics = {"beta": beta(result["net"][1:], market[1:])}
    if chosen.exposure == "long-only":
        exposure_metrics |= active_metrics(result["net"][1:], market[1:], periods)
    book = "long-only" if chosen.exposure == "long-only" else f"{chosen.exposure} long-short"
    return {
        "kind": "backtest",
        "title": f"{chosen.signal} ({signals.describe(signal_parameters)}) {book} backtest",
        "period": [panel.dates[0].isoformat(), panel.dates[-1].isoformat()],
        "bars": len(panel.dates),
        "assets": len(panel.assets),
        "parameters": {
            "signal": chosen.signal,
            **signal_parameters,
            "cost_bps": chosen.cost_bps,
            "lag": chosen.lag,
            "rebalance_every": chosen.rebalance_every,
            "delisting_return": chosen.delisting_return,
            "exposure": chosen.exposure,
        },
        "metrics": {
            **summarize(result["net"], periods),
            **exposure_metrics,
            "gross_annualized_return": summarize(result["gross"], periods)["annualized_return"],
            "average_turnover": float(result["turnover"][1:].mean()),
            "annual_turnover": float(result["turnover"][1:].mean()) * periods,
            "forced_exits": float(result["forced_exits"].sum()),
        },
        "series": {
            "dates": [day.isoformat() for day in panel.dates],
            "net_returns": [float(value) for value in result["net"]],
        },
        "conventions": {
            "periods_per_year": periods,
            "timing": f"signal at close t, traded at close t + {chosen.lag}, as in a study",
            "rebalance": f"every {chosen.rebalance_every} bars; holdings drift in between",
            "costs": f"{chosen.cost_bps} bps per unit of one-way turnover, on actual trades",
            "delistings": f"a delisted holding earns {chosen.delisting_return:+.0%} once",
            "weights": WEIGHTS[chosen.exposure],
            "market": "equal-weight mean of assets priced at the previous close",
        },
        "limitations": LIMITATIONS,
    }
