"""Synthetic price panels for demos and tests, optionally with a planted predictive signal."""

from __future__ import annotations

import numpy as np

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


def dates(n_bars: int) -> list[str]:
    """Return ``n_bars`` labels for synthetic bars (not calendar dates)."""
    return [f"bar-{index:05d}" for index in range(n_bars)]
