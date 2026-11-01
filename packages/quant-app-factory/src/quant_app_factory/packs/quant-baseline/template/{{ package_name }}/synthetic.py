"""Synthetic price panels for demos and tests, optionally with a planted predictive signal."""

from __future__ import annotations

import datetime as dt

import numpy as np

from .data import Panel
from .metrics import FloatArray


def generate_panel(
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
    prices = 100.0 * np.cumprod(1.0 + returns, axis=0)
    return prices, signal


def dates(n_bars: int, start: dt.date = dt.date(2020, 1, 1)) -> list[dt.date]:
    """Return ``n_bars`` weekdays from ``start`` (holidays are ignored)."""
    days: list[dt.date] = []
    day = start
    while len(days) < n_bars:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def demo_panel() -> Panel:
    """A random walk with dates: no signal can have an edge on it, so results near zero are
    expected."""
    prices, _ = generate_panel()
    assets = [f"ASSET{index:02d}" for index in range(prices.shape[1])]
    return Panel(dates=dates(prices.shape[0]), assets=assets, prices=prices)
