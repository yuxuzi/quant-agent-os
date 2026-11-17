"""A backtest of signal-driven weights: lagged trades, a rebalance schedule, drift, and costs.

Timing convention: a signal is known at the close of bar t. With ``lag`` bars of delay, the
weights built from it are first held over bar t + 1 + lag. ``lag=0`` trades at the signal's
own close (the most optimistic choice); the default, `TRADE_LAG`, trades at the next close. A
signal study uses the same convention, so its IC describes what this backtest can earn.

Rebalancing: the book trades to its target weights every ``rebalance_every`` bars, counted from
the first close with a position to take. In between, holdings drift with prices, and every
rebalance trades the target minus the drifted holdings. Costs are charged on those trades.

Missing prices: an asset can only be traded at a close where it has a price, so a position is
carried through a gap and earns the move over the gap when prices resume. A position in an
asset whose prices stop for good earns ``delisting_return`` once and is then written off, at no
trading cost.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import bar_returns
from quant_toolbox.research._evaluation import DELISTING_RETURN, TRADE_LAG


@dataclass(frozen=True)
class BacktestResult:
    """Per-bar results of a backtest; ``held[t]`` is the book over bar t, as weights of its value
    at the start of the bar."""

    gross: FloatArray
    net: FloatArray
    turnover: FloatArray
    held: FloatArray
    forced_exits: FloatArray


def run_backtest(
    prices: FloatArray,
    weights: FloatArray,
    *,
    cost_bps: float,
    lag: int = TRADE_LAG,
    rebalance_every: int = 1,
    delisting_return: float = DELISTING_RETURN,
) -> BacktestResult:
    """Return per-bar gross and net returns, turnover, the weights held, and forced exits.

    ``cost_bps`` is charged per unit of one-way turnover, on actual trades.
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
    return BacktestResult(gross=gross, net=net, turnover=traded, held=held, forced_exits=forced)


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
