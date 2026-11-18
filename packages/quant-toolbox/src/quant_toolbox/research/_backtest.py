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

Costs: ``cost_bps`` is charged per unit of one-way turnover, on actual trades, either one rate
for every asset or one rate per asset. ``costs[t]`` is the charge over bar t as a fraction of the
book, so ``net = gross - costs``. `cost_drag` summarizes what costs take from a backtest.

Splits: `walk_forward_splits` gives rolling train and test ranges, and `purged_kfold_splits`
contiguous test folds with every other bar as training (López de Prado, 2018, ch. 7). ``purge``
drops the training bars just before a test range, whose labels (forward returns over several
bars) would overlap it; ``embargo`` drops the training bars just after a test fold, whose
features may still carry its information.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import bar_returns
from quant_toolbox.research._evaluation import DELISTING_RETURN, TRADE_LAG

IntArray = npt.NDArray[np.intp]


@dataclass(frozen=True)
class BacktestResult:
    """Per-bar results of a backtest; ``held[t]`` is the book over bar t, as weights of its value
    at the start of the bar."""

    gross: FloatArray
    net: FloatArray
    turnover: FloatArray
    held: FloatArray
    forced_exits: FloatArray
    costs: FloatArray = field(default_factory=lambda: np.zeros(0))  # charged per bar, of the book


@dataclass(frozen=True)
class CostDrag:
    """What trading costs take from a backtest, annualised.

    Returns are arithmetic means per bar times ``periods_per_year``; turnover is one-way.
    """

    gross_return: float
    net_return: float
    cost_drag: float  # gross minus net
    annual_turnover: float
    cost_per_turnover_bps: float  # the cost charged per unit of one-way turnover, on average
    break_even_bps: float  # the one rate per unit of turnover at which the mean net return is 0


def run_backtest(
    prices: FloatArray,
    weights: FloatArray,
    *,
    cost_bps: float | FloatArray,
    lag: int = TRADE_LAG,
    rebalance_every: int = 1,
    delisting_return: float = DELISTING_RETURN,
) -> BacktestResult:
    """Return per-bar gross and net returns, turnover, the weights held, and forced exits.

    ``cost_bps`` is charged per unit of one-way turnover, on actual trades: one rate, or an
    array with one rate per asset.
    """
    if lag < 0:
        raise ValueError("lag must be zero or more bars.")
    if rebalance_every < 1:
        raise ValueError("rebalance_every must be at least one bar.")
    rates = np.asarray(cost_bps, dtype=np.float64)
    if rates.ndim not in (0, 1) or (rates.ndim == 1 and rates.size != weights.shape[-1]):
        raise ValueError("cost_bps must be one rate, or one rate per asset (column of weights).")
    asset_returns, exits = bar_returns(prices, delisting_return)
    priced = np.isfinite(prices)
    gone = np.cumsum(exits, axis=0) > 0  # delisted on or before this bar
    bars, assets = int(weights.shape[0]), int(weights.shape[-1])
    held: FloatArray = np.zeros((bars, assets))
    gross: FloatArray = np.zeros(bars)
    traded: FloatArray = np.zeros(bars)
    charged: FloatArray = np.zeros(bars)  # per-asset rates: sum of |trade| * rate / 2, in bps
    drifted: FloatArray = np.zeros(assets)  # the book at the last close, before trading
    first: int | None = None
    for bar in range(1, bars):
        close = bar - 1  # trades for this bar happen at the previous close
        target = weights[close - lag] if close >= lag else np.zeros(assets)
        if first is None and np.any(target != 0.0):
            first = close
        due = first is not None and (close - first) % rebalance_every == 0
        before = drifted * ~gone[close]  # a written-off position is simply gone
        held[bar] = np.where(due & priced[close], target, before)
        trades = np.abs(held[bar] - before)
        traded[bar] = float(trades.sum()) / 2.0
        if rates.ndim == 1:
            charged[bar] = float(trades @ rates) / 2.0
        gross[bar] = float(held[bar] @ asset_returns[bar])
        growth = 1.0 + gross[bar]
        if growth > 0.0:
            drifted = held[bar] * (1.0 + asset_returns[bar]) / growth
        else:  # the book lost everything
            drifted = np.zeros(assets)
    costs = traded * float(rates) / 10_000.0 if rates.ndim == 0 else charged / 10_000.0
    net = gross - costs
    forced = (exits & (held != 0.0)).sum(axis=1).astype(np.float64)
    return BacktestResult(
        gross=gross, net=net, turnover=traded, held=held, forced_exits=forced, costs=costs
    )


def cost_drag(result: BacktestResult, periods_per_year: int) -> CostDrag:
    """Gross and net annualised mean return, cost drag, turnover, and the break-even cost.

    The break-even cost is ``mean(gross) / mean(turnover)`` in basis points: the one rate per
    unit of one-way turnover that would take the mean net return to zero. It and the cost per
    unit of turnover are NaN without turnover. Bar 0, before any trade, is included.
    """
    gross = float(np.mean(result.gross)) if result.gross.size else math.nan
    net = float(np.mean(result.net)) if result.net.size else math.nan
    turnover = float(np.mean(result.turnover)) if result.turnover.size else math.nan
    traded = turnover > 0.0
    return CostDrag(
        gross_return=gross * periods_per_year,
        net_return=net * periods_per_year,
        cost_drag=(gross - net) * periods_per_year,
        annual_turnover=turnover * periods_per_year,
        cost_per_turnover_bps=(gross - net) / turnover * 10_000.0 if traded else math.nan,
        break_even_bps=gross / turnover * 10_000.0 if traded else math.nan,
    )


def walk_forward_splits(
    n_bars: int,
    train: int,
    test: int,
    step: int | None = None,
    start: int = 0,
    *,
    purge: int = 0,
    embargo: int = 0,
) -> list[tuple[range, range]]:
    """Rolling ``(train, test)`` bar ranges from bar ``start``; each test follows its train.

    ``purge`` drops the last bars of each training range, so labels that span several bars do
    not reach into the test. ``embargo`` matters only when training data follows a test, as in
    `purged_kfold_splits`; here it is accepted, checked, and has no effect.
    """
    if train <= 0 or test <= 0:
        raise ValueError("train and test must be positive numbers of bars.")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be zero or more bars.")
    if purge >= train:
        raise ValueError(f"purge ({purge}) must be shorter than the training range ({train}).")
    splits = []
    while start + train + test <= n_bars:
        splits.append(
            (range(start, start + train - purge), range(start + train, start + train + test))
        )
        start += step or test
    return splits


def purged_kfold_splits(
    n_bars: int, folds: int, *, purge: int = 0, embargo: int = 0
) -> list[tuple[IntArray, range]]:
    """K contiguous test folds, each trained on every other bar less a purge and an embargo.

    Returns ``(train, test)`` per fold: ``train`` is a sorted array of bar indices and ``test``
    a range. The first ``n_bars % folds`` folds are one bar longer. ``purge`` bars before each
    test fold and ``embargo`` bars after it are left out of its training set (López de Prado,
    2018, ch. 7). Training data after the test is still later data: use these splits to score
    a model, never to report what a strategy would have earned.
    """
    if folds < 2 or folds > n_bars:
        raise ValueError(f"folds must be from 2 to the number of bars ({n_bars}); got {folds}.")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be zero or more bars.")
    sizes = np.full(folds, n_bars // folds)
    sizes[: n_bars % folds] += 1
    bounds = np.concatenate(([0], np.cumsum(sizes)))
    bars = np.arange(n_bars)
    splits = []
    for first, end in itertools.pairwise(bounds):
        keep = (bars < first - purge) | (bars >= end + embargo)
        train: IntArray = bars[keep]
        splits.append((train, range(int(first), int(end))))
    return splits
