"""Bias checks a project's tests call in one line each, for its own signals and strategies.

A template that quietly leaks future data does more harm than none, because it looks finished.
These checks catch the common ways: a signal that reads later prices, a strategy whose past
returns move when the future changes, costs that ever add return, and an edge on data that cannot
have one. Each raises ``AssertionError`` with a message saying what failed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.data import random_walk_panel
from quant_toolbox.research import look_ahead_bars

Signal = Callable[[FloatArray], FloatArray]
Strategy = Callable[[FloatArray], FloatArray]  # prices (bars by assets) to returns per bar

__all__ = [
    "Signal",
    "Strategy",
    "assert_costs_only_cost",
    "assert_future_does_not_change_the_past",
    "assert_no_edge_on_random_walks",
    "assert_no_look_ahead",
]


def assert_no_look_ahead(signal: Signal, prices: FloatArray | None = None) -> None:
    """A signal at bar t uses prices up to bar t only: cutting later prices changes nothing."""
    data = random_walk_panel(300, 12, seed=3)[0] if prices is None else prices
    bad = look_ahead_bars(signal, data)
    assert not bad, (
        f"The signal looks ahead: its value at bar {bad[0]} changes when later prices are removed."
    )


def assert_future_does_not_change_the_past(
    strategy: Strategy, prices: FloatArray | None = None, cut: int | None = None
) -> None:
    """Changing prices after bar ``cut`` leaves every return before it unchanged."""
    data = random_walk_panel(200, 10, seed=5)[0] if prices is None else prices
    at = data.shape[0] * 3 // 4 if cut is None else cut
    changed = data.copy()
    changed[at:] *= np.linspace(0.5, 1.5, data.shape[1])
    before, after = strategy(data)[:at], strategy(changed)[:at]
    moved = np.flatnonzero(~np.isclose(before, after, equal_nan=True, rtol=0.0, atol=0.0))
    assert moved.size == 0, (
        f"Returns before bar {at} change when later prices change, first at bar {moved[0]}: "
        "the strategy uses data it would not have had."
    )


def assert_costs_only_cost(
    strategy_at_cost: Callable[[float], FloatArray], costs: Sequence[float] = (0.0, 5.0, 50.0)
) -> None:
    """Total return falls as the cost per trade (in basis points) rises."""
    totals = [float(np.nansum(strategy_at_cost(cost))) for cost in costs]
    for (low, high), (left, right) in zip(pairwise(costs), pairwise(totals), strict=True):
        assert right <= left, f"Raising costs from {low} to {high} bps raised returns."


def assert_no_edge_on_random_walks(
    strategy: Strategy, *, seeds: int = 40, bars: int = 500, assets: int = 20
) -> None:
    """On random walks nothing can earn: the mean return across seeds is within 3 errors of 0."""
    means = []
    for seed in range(seeds):
        prices, _ = random_walk_panel(bars, assets, seed=seed)
        means.append(float(np.nanmean(strategy(prices))))
    error = float(np.std(means, ddof=1)) / np.sqrt(len(means))
    average = float(np.mean(means))
    assert abs(average) < 3 * error, (
        f"Mean return {average:.2e} on random walks is more than three standard errors "
        f"({error:.2e}) from zero: the strategy finds an edge where none can exist."
    )
