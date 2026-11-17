"""Synthetic prices, returns, and books for demos and tests. They describe no real fund."""

from __future__ import annotations

import datetime as dt

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.data._calendar import business_days
from quant_toolbox.data._tables import Panel, Table


def random_walk_panel(
    n_bars: int = 750,
    n_assets: int = 30,
    *,
    seed: int = 7,
    signal_strength: float = 0.0,
    volatility: float = 0.01,
    delay: int = 0,
) -> tuple[FloatArray, FloatArray]:
    """Return ``(prices, signal)``, both bars by assets.

    ``signal[t]`` is known at the close of bar t and, when ``signal_strength`` is above zero,
    predicts the return of bar t + 1 + ``delay``. With ``delay=0`` the edge is gone by the time
    a lagged trade can act on it; with ``delay`` equal to the trade lag, it is tradable. With
    zero strength, prices are a pure random walk.
    """
    if delay < 0:
        raise ValueError("delay must be zero or more bars.")
    generator = np.random.default_rng(seed)
    signal = generator.standard_normal((n_bars, n_assets))
    noise = generator.standard_normal((n_bars, n_assets)) * volatility
    returns = noise.copy()
    returns[0] = 0.0
    shift = 1 + delay
    if n_bars > shift:
        returns[shift:] += signal_strength * volatility * signal[:-shift]
    prices = np.asarray(100.0 * np.cumprod(1.0 + returns, axis=0), dtype=np.float64)
    return prices, np.asarray(signal, dtype=np.float64)


def demo_panel() -> Panel:
    """A random walk with dates: no signal can have an edge on it, so results near zero are
    expected."""
    prices, _ = random_walk_panel()
    assets = [f"ASSET{index:02d}" for index in range(prices.shape[1])]
    return Panel(business_days(prices.shape[0], dt.date(2020, 1, 1)), assets, prices)


def demo_returns(count: int = 1000, seed: int = 11) -> Table:
    """Three random-walk books and an index: any edge or pattern in them is chance."""
    generator = np.random.default_rng(seed)
    market = generator.normal(0.0003, 0.009, count)
    books = np.column_stack(
        [
            0.2 * market + generator.normal(0.0004, 0.004, count),
            0.6 * market + generator.normal(0.0002, 0.006, count),
            generator.normal(0.0003, 0.005, count),
            market,
        ]
    )
    return Table(business_days(count), ["Equity L/S", "Macro", "Stat Arb", "Index"], books)


def demo_book(
    count: int = 400, assets: int = 12, seed: int = 21
) -> tuple[Table, Table, dict[str, str]]:
    """Return ``(positions, prices, groups)``: random weights and random-walk prices."""
    generator = np.random.default_rng(seed)
    names = [f"ASSET{index:02d}" for index in range(assets)]
    days = business_days(count, dt.date(2022, 1, 3))
    market = generator.normal(0.0003, 0.009, count)
    loadings = generator.uniform(0.5, 1.4, assets)
    returns = market[:, None] * loadings + generator.normal(0.0, 0.012, (count, assets))
    prices = 100.0 * np.cumprod(1.0 + returns, axis=0)
    base = generator.normal(0.0, 0.08, assets)
    moves = generator.normal(0.0, 0.002, (count, assets))
    weights = base + np.cumsum(moves, axis=0)
    all_prices = np.column_stack([prices, 100.0 * np.cumprod(1.0 + market)])
    sectors = ["Technology", "Financials", "Energy", "Health"]
    groups = {name: sectors[index % len(sectors)] for index, name in enumerate(names)}
    return (
        Table(days, names, weights),
        Table(days, [*names, "Index"], all_prices),
        groups,
    )
